"""The cache seam, and the property that makes it safe to add reads to later.

Storing values is easy. The failure this design exists to prevent is a cached
read added months from now that nobody wires an invalidation for — because that
failure is silent, and a stale roll-up served as current is the same class of
defect as a swallowed exception. It reports a success the system never had.

So the tests below are mostly not about caching. They are about whether it is
*possible* to cache something without saying what it derives from, and whether a
write can invalidate reads it has never heard of.
"""

from __future__ import annotations

import pytest
from lore_eden.cache import (
    MISS,
    Cache,
    DerivedRead,
    DerivedReadRegistry,
    DictCache,
    Lookup,
    NullCache,
)
from lore_eden.store.memory import InMemoryWorkItemStore
from lore_eden.store.records import WorkItemRecord, WorkItemType


class TestAMissIsNotANone:
    def test_a_cached_none_is_distinguishable_from_an_empty_cache(self) -> None:
        """The bug this shape removes: returning ``None`` for a miss makes a
        legitimately cached ``None`` — no roll-up, an empty portfolio, zero
        prerequisites — recompute forever, correctly, and invisibly."""
        cache = DictCache()
        cache.set("empty-rollup", None, frozenset({"item:1"}))

        found = cache.get("empty-rollup")

        assert found.found is True
        assert found.value is None
        assert cache.get("never-set") == MISS

    def test_a_falsey_value_is_still_a_hit(self) -> None:
        cache = DictCache()
        cache.set("zero", 0, frozenset({"item:1"}))
        cache.set("empty", [], frozenset({"item:1"}))

        assert cache.get("zero") == Lookup(found=True, value=0)
        assert cache.get("empty") == Lookup(found=True, value=[])


class TestYouCannotCacheWithoutSayingWhatItDerivesFrom:
    def test_a_derived_read_cannot_be_built_without_tags(self) -> None:
        """The declaration is the constructor, not a convention. There is no
        overload that accepts less, which is what makes 'someone will forget'
        not a failure mode."""
        with pytest.raises(TypeError):
            DerivedRead("rollup", lambda item_id: 1)  # type: ignore[call-arg]

    def test_the_cache_refuses_an_entry_with_no_tags(self) -> None:
        """Belt to the constructor's braces: a host writing to the cache
        directly still cannot store something nothing could invalidate."""
        cache = DictCache()

        with pytest.raises(ValueError, match="no tags"):
            cache.set("orphan", 1, frozenset())

    def test_two_reads_cannot_share_a_name(self) -> None:
        """They would share a key and answer each other."""
        registry = DerivedReadRegistry()
        registry.register(DerivedRead("rollup", lambda x: x, lambda x: frozenset({"a"})))

        with pytest.raises(ValueError, match="already registered"):
            registry.register(DerivedRead("rollup", lambda x: x, lambda x: frozenset({"b"})))

    def test_every_registered_read_declares_tags(self) -> None:
        """The enumeration a reviewer would otherwise have to do by eye. It is
        the reason reads are registered rather than being free functions."""
        registry = DerivedReadRegistry()
        registry.register(DerivedRead("a", lambda x: x, lambda x: frozenset({f"item:{x}"})))
        registry.register(DerivedRead("b", lambda x: x, lambda x: frozenset({f"item:{x}"})))

        for name, read in registry.all_reads().items():
            declared = read.tags("anything")
            assert declared, f"{name} declares no dependencies"


class TestTheNullDefaultChangesNothingButSpeed:
    @staticmethod
    def counting_read() -> tuple[DerivedRead, list[int]]:
        calls: list[int] = []

        def compute(item_id: int) -> int:
            calls.append(item_id)
            return item_id * 2

        return DerivedRead("double", compute, lambda item_id: frozenset({f"item:{item_id}"})), calls

    @pytest.mark.parametrize("cache", [NullCache(), DictCache()], ids=["null", "dict"])
    def test_the_answer_is_the_same_either_way(self, cache: Cache) -> None:
        read, _ = self.counting_read()
        registry = DerivedReadRegistry()
        registry.register(read)

        assert registry.value_of(read, cache, [21]) == 42
        assert registry.value_of(read, cache, [21]) == 42

    def test_the_null_cache_recomputes_and_the_dict_one_does_not(self) -> None:
        """The control. Without it, every parametrised test above would pass
        just as well if the dict cache never stored anything."""
        registry = DerivedReadRegistry()

        null_read, null_calls = self.counting_read()
        registry.register(null_read)
        for _ in range(3):
            registry.value_of(null_read, NullCache(), [1])

        dict_read, dict_calls = self.counting_read()
        DerivedReadRegistry().register(dict_read)
        cache = DictCache()
        for _ in range(3):
            registry.value_of(dict_read, cache, [1])

        assert len(null_calls) == 3
        assert len(dict_calls) == 1


class TestWritesInvalidateReadsTheyHaveNeverHeardOf:
    """The half that scales.

    A write path knows which entities it touched. It does not know which cached
    reads exist, and must not have to: that list is the thing that goes stale
    when someone adds a read.
    """

    @staticmethod
    def registry_over(store: InMemoryWorkItemStore):
        registry = DerivedReadRegistry()
        reads = {
            "child_count": DerivedRead(
                "child_count",
                lambda parent_id: len(store.children_of([parent_id])[parent_id]),
                lambda parent_id: frozenset({f"children:{parent_id}"}),
            ),
            "child_titles": DerivedRead(
                "child_titles",
                lambda parent_id: sorted(
                    child.title for child in store.children_of([parent_id])[parent_id]
                ),
                lambda parent_id: frozenset({f"children:{parent_id}"}),
            ),
            "open_priority": DerivedRead(
                "open_priority",
                lambda parent_id: min(
                    (child.priority for child in store.children_of([parent_id])[parent_id]),
                    default=99,
                ),
                lambda parent_id: frozenset({f"children:{parent_id}"}),
            ),
        }
        for read in reads.values():
            registry.register(read)
        return registry, reads

    def test_a_mutation_is_reflected_in_every_cached_derived_value(self) -> None:
        """AC in full: not "the one I remembered to check" — every read the
        registry holds."""
        store = InMemoryWorkItemStore()
        parent = store.save_item(
            WorkItemRecord(
                external_id="p", title="P", item_type=WorkItemType.CAPABILITY, state="backlog"
            )
        )
        store.save_item(
            WorkItemRecord(
                external_id="c1",
                title="first",
                item_type=WorkItemType.TASK,
                state="backlog",
                parent_id=parent.id,
                priority=3,
            )
        )
        registry, reads = self.registry_over(store)
        cache = DictCache()

        before = {
            name: registry.value_of(read, cache, [parent.id]) for name, read in reads.items()
        }
        assert before == {"child_count": 1, "child_titles": ["first"], "open_priority": 3}

        store.save_item(
            WorkItemRecord(
                external_id="c2",
                title="second",
                item_type=WorkItemType.TASK,
                state="backlog",
                parent_id=parent.id,
                priority=1,
            )
        )
        # The write knows only what it touched.
        cache.invalidate_tags(frozenset({f"children:{parent.id}"}))

        after = {
            name: registry.value_of(read, cache, [parent.id]) for name, read in reads.items()
        }
        assert after == {
            "child_count": 2,
            "child_titles": ["first", "second"],
            "open_priority": 1,
        }

    def test_without_the_invalidation_every_one_of_them_is_stale(self) -> None:
        """The control that gives the test above its meaning: the assertions
        would pass on a cache that never stored anything, so prove they can
        fail."""
        store = InMemoryWorkItemStore()
        parent = store.save_item(
            WorkItemRecord(
                external_id="p", title="P", item_type=WorkItemType.CAPABILITY, state="backlog"
            )
        )
        registry, reads = self.registry_over(store)
        cache = DictCache()
        for read in reads.values():
            registry.value_of(read, cache, [parent.id])

        store.save_item(
            WorkItemRecord(
                external_id="c",
                title="new",
                item_type=WorkItemType.TASK,
                state="backlog",
                parent_id=parent.id,
            )
        )

        assert registry.value_of(reads["child_count"], cache, [parent.id]) == 0

    def test_invalidation_is_scoped_to_what_the_write_touched(self) -> None:
        """A write to one parent must not clear the whole cache. Over-broad
        invalidation is correct and is how a cache quietly stops being one."""
        cache = DictCache()
        cache.set("a", 1, frozenset({"children:p1"}))
        cache.set("b", 2, frozenset({"children:p2"}))

        dropped = cache.invalidate_tags(frozenset({"children:p1"}))

        assert dropped == 1
        assert cache.get("a") == MISS
        assert cache.get("b").value == 2

    def test_an_entry_with_several_tags_goes_when_any_of_them_does(self) -> None:
        cache = DictCache()
        cache.set("rollup", 5, frozenset({"children:p1", "item:x"}))

        assert cache.invalidate_tags(frozenset({"item:x"})) == 1
        assert cache.get("rollup") == MISS

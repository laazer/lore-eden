"""The work-item engine: what it refuses, and what it recomputes.

The engine is the half of `crucible` that is judgement rather than storage —
which parent is legal, which edge would close a cycle, and how a subtree rolls
up. Each of those is a thing a host would otherwise rewrite, and each has a
failure mode that only shows later:

- a bad parent renders as a tree that cannot be drawn;
- a cycle makes every ordering read defend against one forever;
- a stale roll-up is a wrong number presented as the current state of a project.
"""

from __future__ import annotations

import inspect
from typing import Iterator

import pytest
from lore_eden.cache import DictCache, NullCache
from lore_eden.store.memory import InMemoryDependencyStore, InMemoryWorkItemStore
from lore_eden.store.records import WorkItemRecord, WorkItemState, WorkItemType
from lore_eden.work_items import (
    DependencyCycleError,
    InvalidParentError,
    StateVocabulary,
    UnknownStateError,
    WorkItemEngine,
)

#: loregarden's five.
FIVE = StateVocabulary(
    states=tuple(
        WorkItemState(name)
        for name in ("backlog", "in_progress", "blocked", "done", "wont_do")
    ),
    done=frozenset({WorkItemState("done"), WorkItemState("wont_do")}),
)

#: crucible's six. Neither vocabulary contains the other, which is the whole
#: reason the engine holds neither.
SIX = StateVocabulary(
    states=tuple(
        WorkItemState(name)
        for name in ("BACKLOG", "READY", "ACTIVE", "STALLED", "VALIDATION", "RESOLVED")
    ),
    done=frozenset({WorkItemState("RESOLVED")}),
)


def engine(vocabulary: StateVocabulary = FIVE, cache=None) -> WorkItemEngine:
    return WorkItemEngine(
        InMemoryWorkItemStore(), InMemoryDependencyStore(), vocabulary, cache
    )


def item(
    external_id: str,
    item_type: WorkItemType = WorkItemType.TASK,
    state: str = "backlog",
    parent_id: str = "",
) -> WorkItemRecord:
    return WorkItemRecord(
        external_id=external_id,
        title=external_id.upper(),
        item_type=item_type,
        state=WorkItemState(state),
        parent_id=parent_id,
    )


class TestNoDatabaseInTheSignatures:
    """The property that makes the engine liftable at all.

    The codebase this came from passed a live SQLModel `Session` into every
    service, which is what pinned it to one database in one process. A reviewer
    would catch that once; a test catches it every time.
    """

    def test_no_public_method_accepts_a_session_or_engine(self) -> None:
        banned = {"session", "connection", "engine", "conn", "db"}

        for name, method in inspect.getmembers(WorkItemEngine, inspect.isfunction):
            if name.startswith("_"):
                continue
            parameters = set(inspect.signature(method).parameters) - {"self"}
            assert not parameters & banned, f"{name} takes {parameters & banned}"

    def test_the_constructor_takes_stores_not_a_database(self) -> None:
        parameters = list(inspect.signature(WorkItemEngine.__init__).parameters)

        assert parameters == ["self", "items", "dependencies", "vocabulary", "cache"]


class TestTheHierarchyIsEnforced:
    def test_a_milestone_cannot_be_given_a_parent(self) -> None:
        work = engine()
        root = work.save(item("m", WorkItemType.MILESTONE))

        with pytest.raises(InvalidParentError, match="root"):
            work.save(item("m2", WorkItemType.MILESTONE, parent_id=root.id))

    def test_a_task_cannot_hold_a_child(self) -> None:
        work = engine()
        root = work.save(item("m", WorkItemType.MILESTONE))
        feature = work.save(item("f", WorkItemType.FEATURE, parent_id=root.id))
        capability = work.save(item("c", WorkItemType.CAPABILITY, parent_id=feature.id))
        task = work.save(item("t", WorkItemType.TASK, parent_id=capability.id))

        with pytest.raises(InvalidParentError):
            work.save(item("t2", WorkItemType.TASK, parent_id=task.id))

    def test_a_level_cannot_be_skipped(self) -> None:
        work = engine()
        root = work.save(item("m", WorkItemType.MILESTONE))

        with pytest.raises(InvalidParentError, match="not a task"):
            work.save(item("t", WorkItemType.TASK, parent_id=root.id))

    def test_a_bug_hangs_off_anything_above_a_task(self) -> None:
        """A defect is found against the thing it breaks, not against a level."""
        work = engine()
        root = work.save(item("m", WorkItemType.MILESTONE))
        feature = work.save(item("f", WorkItemType.FEATURE, parent_id=root.id))

        assert work.save(item("b1", WorkItemType.BUG, parent_id=root.id))
        assert work.save(item("b2", WorkItemType.BUG, parent_id=feature.id))

    def test_a_missing_parent_is_refused_rather_than_orphaning_the_item(self) -> None:
        work = engine()

        with pytest.raises(InvalidParentError, match="no such parent"):
            work.save(item("f", WorkItemType.FEATURE, parent_id="ghost"))

    def test_a_refusal_writes_nothing(self) -> None:
        """Validation before storage, so a rejected save leaves the store as it
        was rather than half-applied."""
        work = engine()

        with pytest.raises(InvalidParentError):
            work.save(item("f", WorkItemType.FEATURE, parent_id="ghost"))

        assert work.subtree("ghost") == []


class TestTheStateVocabularyBelongsToTheHost:
    @pytest.mark.parametrize(
        ("vocabulary", "state"), [(FIVE, "in_progress"), (SIX, "ACTIVE")], ids=["five", "six"]
    )
    def test_the_engine_runs_unchanged_under_either(self, vocabulary, state) -> None:
        work = engine(vocabulary)

        stored = work.save(item("m", WorkItemType.MILESTONE, state=state))

        assert stored.state == state

    def test_a_state_outside_the_vocabulary_is_refused(self) -> None:
        work = engine(FIVE)

        with pytest.raises(UnknownStateError, match="RESOLVED"):
            work.save(item("m", WorkItemType.MILESTONE, state="RESOLVED"))

    def test_done_is_whatever_the_host_says_it_is(self) -> None:
        """The only judgement the engine makes about a state, and the host makes
        it. Five states count `wont_do` as finished; six do not have it."""
        five = engine(FIVE)
        root = five.save(item("m", WorkItemType.MILESTONE))
        five.save(item("f1", WorkItemType.FEATURE, state="done", parent_id=root.id))
        five.save(item("f2", WorkItemType.FEATURE, state="wont_do", parent_id=root.id))

        assert five.rollup(root.id).completion == 1.0

    def test_a_done_state_outside_the_vocabulary_is_refused_at_construction(self) -> None:
        with pytest.raises(UnknownStateError, match="marked done"):
            StateVocabulary(
                states=(WorkItemState("open"),), done=frozenset({WorkItemState("shipped")})
            )


class TestCyclesAreRefusedOnInsert:
    def test_a_self_edge_is_refused(self) -> None:
        with pytest.raises(DependencyCycleError, match="itself"):
            engine().add_dependency("a", "a")

    def test_a_direct_cycle_is_refused(self) -> None:
        work = engine()
        work.add_dependency("b", "a")

        with pytest.raises(DependencyCycleError):
            work.add_dependency("a", "b")

    def test_a_transitive_cycle_is_refused(self) -> None:
        """The case a direct check misses, and the one that actually happens:
        nobody adds `a waits for b` next to `b waits for a`. They add a third
        edge months later."""
        work = engine()
        work.add_dependency("b", "a")
        work.add_dependency("c", "b")

        with pytest.raises(DependencyCycleError):
            work.add_dependency("a", "c")

    def test_a_long_chain_is_still_caught(self) -> None:
        work = engine()
        chain = [f"n{index}" for index in range(12)]
        for waiter, prerequisite in zip(chain[1:], chain):
            work.add_dependency(waiter, prerequisite)

        with pytest.raises(DependencyCycleError):
            work.add_dependency(chain[0], chain[-1])

    def test_a_diamond_is_not_a_cycle(self) -> None:
        """The control: a shape that shares ancestors must still be allowed, or
        the check is just refusing everything."""
        work = engine()
        work.add_dependency("b", "a")
        work.add_dependency("c", "a")

        assert work.add_dependency("d", "b") is True
        assert work.add_dependency("d", "c") is True

    def test_a_refused_edge_is_not_stored(self) -> None:
        work = engine()
        work.add_dependency("b", "a")

        with pytest.raises(DependencyCycleError):
            work.add_dependency("a", "b")

        assert work.prerequisites(["a"])["a"] == frozenset()


class TestRollupsGoThroughTheCache:
    @staticmethod
    def tree(work: WorkItemEngine) -> tuple[str, str]:
        root = work.save(item("m", WorkItemType.MILESTONE))
        feature = work.save(item("f", WorkItemType.FEATURE, parent_id=root.id))
        work.save(item("c1", WorkItemType.CAPABILITY, state="done", parent_id=feature.id))
        work.save(item("c2", WorkItemType.CAPABILITY, parent_id=feature.id))
        return root.id, feature.id

    def test_a_rollup_counts_descendants_not_the_root(self) -> None:
        work = engine(cache=DictCache())
        root_id, _ = self.tree(work)

        rollup = work.rollup(root_id)

        assert (rollup.total, rollup.done) == (3, 1)
        assert rollup.completion == pytest.approx(1 / 3)

    def test_an_empty_subtree_is_zero_rather_than_an_error(self) -> None:
        work = engine(cache=DictCache())
        root = work.save(item("m", WorkItemType.MILESTONE))

        rollup = work.rollup(root.id)

        assert (rollup.total, rollup.done, rollup.completion) == (0, 0, 0.0)

    def test_changing_a_descendant_changes_the_rollup(self) -> None:
        """The invalidation AC: the cached value reflects the mutation, without
        anyone naming the roll-up at the write site."""
        cache = DictCache()
        work = engine(cache=cache)
        root_id, feature_id = self.tree(work)
        assert work.rollup(root_id).done == 1

        leaf = [child for child in work.subtree(root_id) if child.external_id == "c2"][0]
        work.save(
            WorkItemRecord(
                id=leaf.id,
                external_id=leaf.external_id,
                title=leaf.title,
                item_type=leaf.item_type,
                state=WorkItemState("done"),
                parent_id=leaf.parent_id,
            )
        )

        assert work.rollup(root_id).done == 2

    def test_moving_an_item_updates_both_parents(self) -> None:
        """The tree an item left is as wrong as the one it joined. A write path
        that invalidated only the new parent would leave the old roll-up
        confidently one item too high."""
        cache = DictCache()
        work = engine(cache=cache)
        root = work.save(item("m", WorkItemType.MILESTONE))
        first = work.save(item("f1", WorkItemType.FEATURE, parent_id=root.id))
        second = work.save(item("f2", WorkItemType.FEATURE, parent_id=root.id))
        moving = work.save(item("c", WorkItemType.CAPABILITY, parent_id=first.id))
        assert work.rollup(first.id).total == 1
        assert work.rollup(second.id).total == 0

        work.save(
            WorkItemRecord(
                id=moving.id,
                external_id=moving.external_id,
                title=moving.title,
                item_type=moving.item_type,
                state=moving.state,
                parent_id=second.id,
            )
        )

        assert work.rollup(first.id).total == 0
        assert work.rollup(second.id).total == 1

    def test_the_cached_and_uncached_paths_give_the_same_answer(self) -> None:
        """NullCache is the default, so the two paths cannot drift. Asserted by
        building the same tree twice and comparing the roll-ups field by field,
        rather than by checking that each returns something."""
        cached = engine(cache=DictCache())
        uncached = engine(cache=NullCache())
        cached_root, _ = self.tree(cached)
        uncached_root, _ = self.tree(uncached)

        from_cache = cached.rollup(cached_root)
        from_scratch = uncached.rollup(uncached_root)

        assert (from_cache.total, from_cache.done) == (from_scratch.total, from_scratch.done)
        assert from_cache.by_state == from_scratch.by_state
        assert from_cache.completion == from_scratch.completion

    def test_the_cache_is_actually_used(self) -> None:
        """The control for the test above: without it, every cache assertion
        here would hold just as well if DictCache stored nothing."""
        cache = DictCache()
        work = engine(cache=cache)
        root_id, _ = self.tree(work)

        work.rollup(root_id)
        entries_after_first = len(cache._values)
        work.rollup(root_id)

        assert entries_after_first == 1
        assert cache.get(f"work-item-rollup:{root_id}").found is True

    def test_every_cached_read_declares_dependencies(self) -> None:
        work = engine(cache=DictCache())
        root_id, _ = self.tree(work)

        assert list(work.cached_reads()) == ["work-item-rollup"]
        assert work._rollup_tags(root_id)

    def test_the_portfolio_rolls_up_several_roots(self) -> None:
        work = engine(cache=DictCache())
        first, _ = self.tree(work)
        second = work.save(item("m2", WorkItemType.MILESTONE)).id

        portfolio = work.portfolio([first, second])

        assert portfolio[first].total == 3
        assert portfolio[second].total == 0

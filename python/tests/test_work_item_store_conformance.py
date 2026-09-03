"""One suite, every backend, so a host's storage cannot quietly differ.

The protocols in :mod:`lore_eden.store.protocols` are the whole point of the
work-item engine being liftable: loremaker has Postgres and its own ORM, and the
codebase this came from passed a live SQLModel ``Session`` into every service,
which is precisely what pinned it to one database in one process.

A protocol nothing checks is a suggestion, so every implementation runs the same
tests here:

- **memory** — a dict. Not only a test double; a host running one item at a time
  needs no database.
- **sqlite** — the SQL implementation, on the database it was written against.
- **postgres** — the same implementation on the database loremaker actually
  runs. Skipped when no DSN is configured, *unless*
  ``LORE_EDEN_REQUIRE_POSTGRES=1``, which CI sets: a portability claim proven by
  a skipped test is not proven.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterator

import pytest
from lore_eden.store.memory import (
    InMemoryCycleStore,
    InMemoryDependencyStore,
    InMemoryRelationStore,
    InMemoryTagStore,
    InMemoryWorkItemStore,
)
from lore_eden.store.records import (
    CycleRecord,
    RelationKind,
    RelationRecord,
    WorkItemRecord,
    WorkItemType,
)

POSTGRES_DSN_ENV = "LORE_EDEN_TEST_POSTGRES_DSN"
REQUIRE_POSTGRES_ENV = "LORE_EDEN_REQUIRE_POSTGRES"


@dataclass
class Stores:
    """The five protocols a work-item engine needs, however they are backed."""

    items: Any
    dependencies: Any
    cycles: Any
    tags: Any
    relations: Any
    engine: Any = None


def _sql_stores(url: str) -> Iterator[Stores]:
    from sqlmodel import Session, SQLModel, create_engine

    from lore_eden.store.sql import (
        SqlCycleStore,
        SqlDependencyStore,
        SqlRelationStore,
        SqlTagStore,
        SqlWorkItemStore,
    )

    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield Stores(
            items=SqlWorkItemStore(session),
            dependencies=SqlDependencyStore(session),
            cycles=SqlCycleStore(session),
            tags=SqlTagStore(session),
            relations=SqlRelationStore(session),
            engine=engine,
        )
    SQLModel.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(params=["memory", "sqlite", "postgres"])
def stores(request, tmp_path) -> Iterator[Stores]:
    if request.param == "memory":
        yield Stores(
            items=InMemoryWorkItemStore(),
            dependencies=InMemoryDependencyStore(),
            cycles=InMemoryCycleStore(),
            tags=InMemoryTagStore(),
            relations=InMemoryRelationStore(),
        )
        return

    if request.param == "sqlite":
        yield from _sql_stores(f"sqlite:///{tmp_path / 'conformance.db'}")
        return

    dsn = os.environ.get(POSTGRES_DSN_ENV, "")
    if not dsn:
        if os.environ.get(REQUIRE_POSTGRES_ENV) == "1":
            pytest.fail(
                f"{REQUIRE_POSTGRES_ENV}=1 but {POSTGRES_DSN_ENV} is unset — the "
                "Postgres conformance run would have skipped silently."
            )
        pytest.skip(f"set {POSTGRES_DSN_ENV} to run the Postgres conformance pass")
    yield from _sql_stores(dsn)


def item(external_id: str, **overrides) -> WorkItemRecord:
    fields = {
        "external_id": external_id,
        "title": external_id.upper(),
        "item_type": WorkItemType.TASK,
        "state": "backlog",
    }
    fields.update(overrides)
    return WorkItemRecord(**fields)


class TestItems:
    def test_a_saved_item_reads_back_whole(self, stores):
        saved = stores.items.save_item(
            item("lor-1", item_type=WorkItemType.FEATURE, priority=1, description="why")
        )

        found = stores.items.get_item(saved.id)

        assert found is not None
        assert (found.external_id, found.title, found.priority) == ("lor-1", "LOR-1", 1)
        assert found.item_type is WorkItemType.FEATURE
        assert found.description == "why"

    def test_an_absent_item_is_none_not_an_error(self, stores):
        assert stores.items.get_item("nothing-here") is None

    def test_saving_twice_updates_rather_than_duplicates(self, stores):
        saved = stores.items.save_item(item("lor-2"))

        stores.items.save_item(
            WorkItemRecord(
                id=saved.id,
                external_id="lor-2",
                title="renamed",
                item_type=WorkItemType.TASK,
                state="done",
            )
        )

        assert stores.items.get_item(saved.id).title == "renamed"
        assert len(stores.items.list_items()) == 1

    def test_get_items_omits_what_is_missing_rather_than_inventing_it(self, stores):
        first = stores.items.save_item(item("lor-3"))

        found = stores.items.get_items([first.id, "absent"])

        assert set(found) == {first.id}

    def test_delete_reports_whether_there_was_anything_to_delete(self, stores):
        saved = stores.items.save_item(item("lor-4"))

        assert stores.items.delete_item(saved.id) is True
        assert stores.items.delete_item(saved.id) is False

    def test_children_are_keyed_by_every_parent_asked_about(self, stores):
        """Including the childless ones. A caller that had to tell "no children"
        from "not asked" would have to ask again to find out."""
        parent = stores.items.save_item(item("lor-5", item_type=WorkItemType.CAPABILITY))
        stores.items.save_item(item("lor-6", parent_id=parent.id))
        stores.items.save_item(item("lor-7", parent_id=parent.id))
        childless = stores.items.save_item(item("lor-8", item_type=WorkItemType.CAPABILITY))

        found = stores.items.children_of([parent.id, childless.id])

        assert set(found) == {parent.id, childless.id}
        assert {child.external_id for child in found[parent.id]} == {"lor-6", "lor-7"}
        assert list(found[childless.id]) == []

    def test_filters_combine_rather_than_replace_each_other(self, stores):
        stores.items.save_item(item("lor-9", state="done"))
        wanted = stores.items.save_item(
            item("lor-10", state="done", item_type=WorkItemType.BUG)
        )
        stores.items.save_item(item("lor-11", item_type=WorkItemType.BUG))

        found = stores.items.list_items(state="done", item_type=WorkItemType.BUG)

        assert [record.id for record in found] == [wanted.id]

    def test_the_limit_is_honoured(self, stores):
        for index in range(5):
            stores.items.save_item(item(f"lor-many-{index}"))

        assert len(stores.items.list_items(limit=2)) == 2


class TestDependencies:
    def test_an_edge_is_idempotent_and_readable_both_ways(self, stores):
        assert stores.dependencies.add_dependency("b", "a") is True
        assert stores.dependencies.add_dependency("b", "a") is False

        assert stores.dependencies.prerequisites_map(["b"])["b"] == frozenset({"a"})
        assert stores.dependencies.dependents_map(["a"])["a"] == frozenset({"b"})

    def test_removing_reports_whether_there_was_an_edge(self, stores):
        stores.dependencies.add_dependency("b", "a")

        assert stores.dependencies.remove_dependency("b", "a") is True
        assert stores.dependencies.remove_dependency("b", "a") is False

    def test_every_id_asked_about_is_a_key(self, stores):
        stores.dependencies.add_dependency("b", "a")

        found = stores.dependencies.prerequisites_map(["a", "b", "c"])

        assert set(found) == {"a", "b", "c"}
        assert found["a"] == frozenset()
        assert found["c"] == frozenset()

    def test_a_whole_level_is_one_call(self, stores):
        """The shape a transitive walk depends on: ask for a level, not a node.

        It is what keeps the engine's cycle check proportional to the graph's
        depth instead of its size."""
        stores.dependencies.add_dependency("c", "b")
        stores.dependencies.add_dependency("c", "a")
        stores.dependencies.add_dependency("d", "a")

        found = stores.dependencies.prerequisites_map(["c", "d"])

        assert found["c"] == frozenset({"a", "b"})
        assert found["d"] == frozenset({"a"})


class TestCycles:
    def test_a_cycle_round_trips(self, stores):
        saved = stores.cycles.save_cycle(CycleRecord(name="sprint 1", state="active"))

        found = stores.cycles.get_cycle(saved.id)

        assert found is not None
        assert (found.name, found.state) == ("sprint 1", "active")

    def test_listing_filters_by_state(self, stores):
        stores.cycles.save_cycle(CycleRecord(name="done one", state="completed"))
        active = stores.cycles.save_cycle(CycleRecord(name="live one", state="active"))

        found = stores.cycles.list_cycles(state="active")

        assert [record.id for record in found] == [active.id]


class TestTags:
    def test_tags_keep_the_order_they_were_given(self, stores):
        """Alphabetising would silently reorder a host's own display order."""
        stores.tags.set_tags("item", ["urgent", "backend", "spike"])

        assert stores.tags.tags_for(["item"])["item"] == ("urgent", "backend", "spike")

    def test_setting_replaces_rather_than_adds(self, stores):
        stores.tags.set_tags("item", ["one", "two"])

        stores.tags.set_tags("item", ["three"])

        assert stores.tags.tags_for(["item"])["item"] == ("three",)

    def test_a_repeated_tag_is_stored_once(self, stores):
        stored = stores.tags.set_tags("item", ["dup", "dup", "other"])

        assert stored == ("dup", "other")

    def test_an_untagged_item_is_still_a_key(self, stores):
        assert stores.tags.tags_for(["never-tagged"]) == {"never-tagged": ()}


class TestRelations:
    def test_a_relation_round_trips_with_its_kind(self, stores):
        record = RelationRecord("a", "b", RelationKind.SUPERSEDES)

        assert stores.relations.add_relation(record) is True

        found = stores.relations.relations_for(["a"])["a"]
        assert [(rel.related_id, rel.kind) for rel in found] == [
            ("b", RelationKind.SUPERSEDES)
        ]

    def test_the_same_pair_can_hold_two_kinds(self, stores):
        """Kind is part of the identity: 'duplicates' and 'relates to' between
        the same two items are two facts, not one overwritten."""
        stores.relations.add_relation(RelationRecord("a", "b", RelationKind.RELATES_TO))
        stores.relations.add_relation(RelationRecord("a", "b", RelationKind.DUPLICATES))

        assert len(stores.relations.relations_for(["a"])["a"]) == 2

    def test_adding_the_same_relation_twice_is_reported_not_duplicated(self, stores):
        record = RelationRecord("a", "b", RelationKind.RELATES_TO)
        stores.relations.add_relation(record)

        assert stores.relations.add_relation(record) is False
        assert len(stores.relations.relations_for(["a"])["a"]) == 1

    def test_removing_reports_whether_there_was_one(self, stores):
        record = RelationRecord("a", "b", RelationKind.RELATES_TO)
        stores.relations.add_relation(record)

        assert stores.relations.remove_relation(record) is True
        assert stores.relations.remove_relation(record) is False

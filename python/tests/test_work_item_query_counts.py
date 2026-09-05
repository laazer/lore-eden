"""The batch reads have to stay batch reads.

Every multi-item method could be implemented as a loop over the single-item one
and every conformance test above would still pass. The difference only shows on
a board rendering two hundred cards, in production, as latency nobody can point
at — which is why the query count is asserted rather than trusted to review.

The bound being pinned is *constant*, not merely small: one query whatever the
input size. A test that allowed "a few more for a bigger input" would permit
exactly the N+1 it exists to stop.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from lore_eden.store.records import RelationKind, RelationRecord, WorkItemRecord, WorkItemType
from lore_eden.store.sql import (
    SqlDependencyStore,
    SqlRelationStore,
    SqlTagStore,
    SqlWorkItemStore,
)
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine


class QueryCounter:
    """Counts statements sent to the database, whatever issued them."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def __len__(self) -> int:
        return len(self.statements)

    def selects(self) -> int:
        return sum(1 for sql in self.statements if sql.lstrip().upper().startswith("SELECT"))


@pytest.fixture
def counted(tmp_path) -> Iterator[tuple[Session, QueryCounter]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'counts.db'}")
    SQLModel.metadata.create_all(engine)
    counter = QueryCounter()

    @event.listens_for(engine, "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        counter.statements.append(statement)

    with Session(engine) as session:
        yield session, counter
    engine.dispose()


def make_items(store: SqlWorkItemStore, count: int, parent_id: str = "") -> list[str]:
    return [
        store.save_item(
            WorkItemRecord(
                external_id=f"lor-{index}",
                title=f"item {index}",
                item_type=WorkItemType.TASK,
                state="backlog",
                parent_id=parent_id,
            )
        ).id
        for index in range(count)
    ]


class TestOneQueryWhateverTheInput:
    def test_get_items(self, counted) -> None:
        session, counter = counted
        store = SqlWorkItemStore(session)
        ids = make_items(store, 50)

        counter.statements.clear()
        store.get_items(ids[:1])
        one = counter.selects()

        counter.statements.clear()
        store.get_items(ids)
        many = counter.selects()

        assert (one, many) == (1, 1)

    def test_children_of(self, counted) -> None:
        session, counter = counted
        store = SqlWorkItemStore(session)
        parents = make_items(store, 50)
        for parent in parents[:5]:
            make_items(store, 2, parent_id=parent)

        counter.statements.clear()
        store.children_of(parents[:1])
        one = counter.selects()

        counter.statements.clear()
        store.children_of(parents)
        many = counter.selects()

        assert (one, many) == (1, 1)

    def test_prerequisites_and_dependents(self, counted) -> None:
        session, counter = counted
        store = SqlDependencyStore(session)
        ids = [f"item-{index}" for index in range(50)]
        for item_id in ids:
            store.add_dependency(item_id, "root")

        counter.statements.clear()
        store.prerequisites_map(ids[:1])
        one = counter.selects()

        counter.statements.clear()
        store.prerequisites_map(ids)
        assert (one, counter.selects()) == (1, 1)

        counter.statements.clear()
        store.dependents_map(ids)
        assert counter.selects() == 1

    def test_tags_for(self, counted) -> None:
        session, counter = counted
        store = SqlTagStore(session)
        ids = [f"item-{index}" for index in range(50)]
        for item_id in ids:
            store.set_tags(item_id, ["one", "two"])

        counter.statements.clear()
        store.tags_for(ids[:1])
        one = counter.selects()

        counter.statements.clear()
        store.tags_for(ids)

        assert (one, counter.selects()) == (1, 1)

    def test_relations_for(self, counted) -> None:
        session, counter = counted
        store = SqlRelationStore(session)
        ids = [f"item-{index}" for index in range(50)]
        for item_id in ids:
            store.add_relation(RelationRecord(item_id, "other", RelationKind.RELATES_TO))

        counter.statements.clear()
        store.relations_for(ids[:1])
        one = counter.selects()

        counter.statements.clear()
        store.relations_for(ids)

        assert (one, counter.selects()) == (1, 1)


class TestTheCounterWouldNoticeALoop:
    """The control. Without it, every assertion above would hold just as well
    if the listener were never firing."""

    def test_a_deliberate_n_plus_one_is_counted(self, counted) -> None:
        session, counter = counted
        store = SqlWorkItemStore(session)
        ids = make_items(store, 10)

        counter.statements.clear()
        for item_id in ids:
            store.get_item(item_id)

        assert counter.selects() == 10


class TestEmptyInput:
    """An empty request must not become a query for everything."""

    def test_no_ids_means_no_query(self, counted) -> None:
        session, counter = counted
        items = SqlWorkItemStore(session)
        make_items(items, 3)

        counter.statements.clear()
        assert items.get_items([]) == {}
        assert items.children_of([]) == {}
        assert SqlDependencyStore(session).prerequisites_map([]) == {}
        assert SqlTagStore(session).tags_for([]) == {}
        assert SqlRelationStore(session).relations_for([]) == {}

        assert counter.selects() == 0


class TestTheEngineWalksLevelsNotNodes:
    """Depth, not size, is what the engine's walks may cost.

    A subtree walk and a cycle check are both breadth-first here so each level
    is one query. Written node-at-a-time they would be equally correct and would
    issue one query per node — which is the shape that makes a cycle check too
    expensive to leave switched on, and switching it off is how a graph acquires
    the cycle every later read then has to defend against.
    """

    @staticmethod
    def engine_on(session):
        from lore_eden.store.records import WorkItemState
        from lore_eden.store.sql import SqlDependencyStore, SqlWorkItemStore
        from lore_eden.work_items import StateVocabulary, WorkItemEngine

        vocabulary = StateVocabulary(
            states=(WorkItemState("backlog"), WorkItemState("done")),
            done=frozenset({WorkItemState("done")}),
        )
        return WorkItemEngine(
            SqlWorkItemStore(session), SqlDependencyStore(session), vocabulary
        )

    @staticmethod
    def wide_tree(work, width: int) -> str:
        from lore_eden.store.records import WorkItemRecord, WorkItemState, WorkItemType

        root = work.save(
            WorkItemRecord(
                external_id="m",
                title="M",
                item_type=WorkItemType.MILESTONE,
                state=WorkItemState("backlog"),
            )
        )
        for index in range(width):
            work.save(
                WorkItemRecord(
                    external_id=f"f{index}",
                    title=f"F{index}",
                    item_type=WorkItemType.FEATURE,
                    state=WorkItemState("backlog"),
                    parent_id=root.id,
                )
            )
        return root.id

    def test_a_subtree_walk_costs_the_same_at_ten_children_as_at_a_hundred(
        self, counted
    ) -> None:
        session, counter = counted
        work = self.engine_on(session)
        narrow = self.wide_tree(work, 10)

        counter.statements.clear()
        work.subtree(narrow)
        for_ten = counter.selects()

        wide = self.wide_tree(work, 100)
        counter.statements.clear()
        work.subtree(wide)
        for_a_hundred = counter.selects()

        assert for_ten == for_a_hundred

    def test_a_cycle_check_costs_the_chain_s_depth_not_its_length_squared(
        self, counted
    ) -> None:
        """A 30-link chain: breadth-first is 31 lookups, node-at-a-time is
        hundreds. The assertion is deliberately generous — what it rules out is
        growth proportional to the work, not an exact number."""
        session, counter = counted
        work = self.engine_on(session)
        chain = [f"n{index}" for index in range(30)]
        for waiter, prerequisite in zip(chain[1:], chain, strict=False):
            work.add_dependency(waiter, prerequisite)

        counter.statements.clear()
        from lore_eden.work_items import DependencyCycleError

        with pytest.raises(DependencyCycleError):
            work.add_dependency(chain[0], chain[-1])

        assert counter.selects() <= len(chain) + 2

    def test_the_counter_would_notice_a_node_at_a_time_walk(self, counted) -> None:
        """The control. Without it, the bounds above would hold on a listener
        that never fired."""
        session, counter = counted
        work = self.engine_on(session)
        root = self.wide_tree(work, 10)
        children = work.subtree(root)

        counter.statements.clear()
        for child in children:
            work.subtree(child.id)

        assert counter.selects() >= len(children)

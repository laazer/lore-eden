"""What a harness needs from storage, and nothing about how.

Three protocols. A host with its own database implements them and never
installs the SQL extra; :mod:`lore_eden.store.memory` implements them in a dict
for tests; :mod:`lore_eden.store.sql` implements them on SQLModel for a host
that would rather not.

The protocols come first, and that ordering is the point. The source project has
no storage seam at all — its cursors and approvals live in tables its own
services query directly — which is exactly what makes its orchestration hard to
lift. Whichever schema gets written first becomes the thing everything couples
to, so the interface has to exist before any schema does.

## Reading an override consumes it

:meth:`CursorStore.take_agent_override` returns the pin **and clears it**, in one
call, because the alternative is a caller that reads and forgets to clear. That
is not hypothetical: it is the source's stale-pin dispatch loop, where a stage
re-reads a pin set for a run that already happened and routes to the wrong agent
again.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Mapping, Protocol, Sequence

from lore_eden.store.records import (
    CursorRecord,
    CycleRecord,
    RelationRecord,
    RunRecord,
    RunStatus,
    WorkItemRecord,
    WorkItemState,
    WorkItemType,
)
from lore_eden.workflow.approvals import Approval


class CursorStore(Protocol):
    """Where work items are up to."""

    def get_cursor(self, item_id: str) -> CursorRecord | None: ...

    def save_cursor(self, record: CursorRecord) -> CursorRecord: ...

    def set_agent_override(self, item_id: str, agent_id: str) -> None: ...

    def take_agent_override(self, item_id: str) -> str:
        """The pin, cleared as it is read. Empty when there was none."""
        ...

    def list_cursors(self, *, stage_key: str = "") -> Sequence[CursorRecord]: ...


class RunStore(Protocol):
    """What ran, and how it went."""

    def start_run(self, record: RunRecord) -> RunRecord: ...

    def get_run(self, run_id: str) -> RunRecord | None: ...

    def beat(self, run_id: str, *, now: datetime | None = None) -> RunRecord | None:
        """Mark a run still alive. Silent when the run is gone."""
        ...

    def finish_run(self, record: RunRecord) -> RunRecord: ...

    def list_runs(
        self, *, item_id: str = "", stage_key: str = "", limit: int = 50
    ) -> Sequence[RunRecord]: ...

    def stale_runs(
        self, *, stale_after: timedelta, now: datetime | None = None
    ) -> Sequence[RunRecord]:
        """Runs still marked ``RUNNING`` whose heartbeat has gone quiet.

        The query a host needs to answer "is anything actually going?" — and the
        one whose absence turns a dead run into a blocked work item nobody can
        explain.
        """
        ...


class ApprovalStorage(Protocol):
    """Questions a run stopped to ask.

    Named for the *role* rather than matching
    :class:`lore_eden.workflow.approvals.ApprovalStore`, which is the in-memory
    implementation of it and already the type ``GateService`` accepts. Two names
    for the same idea is worse than one awkward one, but a protocol shadowing a
    concrete class of the same name is worse still — the import that wins would
    depend on which module a host reached for first.
    """

    def add(self, approval: Approval) -> Approval: ...

    def get(self, approval_id: str) -> Approval: ...

    def save(self, approval: Approval) -> Approval: ...

    def pending(
        self, *, subject_type: str = "", subject_id: str = ""  # py-org: allow-string (the host's vocabulary, not this package's)
    ) -> list[Approval]: ...

    def for_subject(self, subject_type: str, subject_id: str) -> list[Approval]: ...  # py-org: allow-string (the host's vocabulary, not this package's)


__all__ = [
    "ApprovalStorage",
    "CursorStore",
    "RunStatus",
    "RunStore",
]


# ---------------------------------------------------------------------------
# The work-item engine's storage.
#
# Two rules run through every signature below, and both are the difference
# between a library a host can adopt and one it has to fork.
#
# **No session, connection or ORM model appears in any of them.** The codebase
# these were lifted from passes a live SQLModel `Session` into every service —
# `TicketDependencies.__init__(self, session)` is the shape throughout — which
# reads as harmless and means the engine only runs on that host's database, on
# SQLite, in that process. A host with Postgres and a different ORM implements
# what is below and installs no extra.
#
# **Every read that answers about more than one item takes a collection.** A
# board rendering two hundred cards asks once. The batch methods return a
# mapping keyed by the id asked for, including keys with nothing against them,
# so a caller never has to distinguish "absent" from "empty" by a second query.
# ---------------------------------------------------------------------------


class WorkItemStore(Protocol):
    """The items themselves, and the tree they hang in."""

    def get_item(self, item_id: str) -> WorkItemRecord | None: ...

    def get_items(self, item_ids: Sequence[str]) -> Mapping[str, WorkItemRecord]:
        """The items that exist, keyed by id. Missing ids are simply absent."""
        ...

    def save_item(self, record: WorkItemRecord) -> WorkItemRecord: ...

    def delete_item(self, item_id: str) -> bool:
        """True when something was deleted, False when there was nothing to."""
        ...

    def children_of(self, parent_ids: Sequence[str]) -> Mapping[str, Sequence[WorkItemRecord]]:
        """Direct children per parent — one query, every key present.

        Depth is the caller's business. A subtree walk asks level by level, so
        its query count follows the tree's depth rather than its size, which is
        the bound that matters: trees get wide long before they get deep.
        """
        ...

    def list_items(
        self,
        *,
        item_type: WorkItemType | None = None,
        state: WorkItemState = WorkItemState(""),
        cycle_id: str = "",
        parent_id: str = "",
        limit: int = 100,
    ) -> Sequence[WorkItemRecord]:
        """Filtered list. Named parameters rather than a filter bag, so a typo
        is a TypeError instead of a filter that silently matched everything."""
        ...


class DependencyStore(Protocol):
    """Which items wait for which.

    Storage only: whether an edge would close a cycle is the engine's question,
    because answering it needs a graph walk and this layer holds edges. What
    this layer guarantees is that the walk can be done in queries proportional
    to the graph's *depth* — :meth:`prerequisites_map` takes a whole level at a
    time.
    """

    def add_dependency(self, item_id: str, depends_on_id: str) -> bool:
        """True when the edge was new. Idempotent — re-adding is not an error."""
        ...

    def remove_dependency(self, item_id: str, depends_on_id: str) -> bool: ...

    def prerequisites_map(self, item_ids: Sequence[str]) -> Mapping[str, frozenset[str]]:
        """What each id waits for. Every key present, empty when nothing."""
        ...

    def dependents_map(self, item_ids: Sequence[str]) -> Mapping[str, frozenset[str]]:
        """The same edges read the other way — what waits on each id."""
        ...


class CycleStore(Protocol):
    """Iterations, by whatever name the host calls a sprint."""

    def get_cycle(self, cycle_id: str) -> CycleRecord | None: ...

    def save_cycle(self, record: CycleRecord) -> CycleRecord: ...

    def list_cycles(
        self, *, state: WorkItemState = WorkItemState("")
    ) -> Sequence[CycleRecord]: ...


class TagStore(Protocol):
    """Free-form labels on items."""

    def tags_for(self, item_ids: Sequence[str]) -> Mapping[str, tuple[str, ...]]:
        """Tags per id, every key present."""
        ...

    def set_tags(self, item_id: str, tags: Sequence[str]) -> tuple[str, ...]:
        """Replace an item's tags outright, and report what it now carries."""
        ...


class RelationStore(Protocol):
    """Links that do not order anything.

    Separate from :class:`DependencyStore` on purpose. A relation carries no
    scheduling meaning, so it needs no cycle check — and keeping it in the same
    table as the edges that do is how "relates to" ends up silently blocking a
    queue.
    """

    def add_relation(self, record: RelationRecord) -> bool: ...

    def remove_relation(self, record: RelationRecord) -> bool: ...

    def relations_for(
        self, item_ids: Sequence[str]
    ) -> Mapping[str, Sequence[RelationRecord]]: ...

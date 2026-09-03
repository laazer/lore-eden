"""What a harness stores, as plain values.

No database import anywhere in this module, and that is enforced by a test. A
host with its own persistence implements the protocols in
:mod:`lore_eden.store.protocols` against these records and never installs the
SQL extra.

## A run that died is not a run that is going

The distinction this module exists to make. A process holding a `running` row
that is then killed — a machine reboot, a container eviction, an operator's
Ctrl-C — leaves that row saying `running` forever. Nothing else in the system
can tell it apart from work genuinely in flight, so:

- a UI shows it as busy indefinitely,
- a queue will not start the next item because one is "already going",
- and the work item it belongs to reads as blocked, which is how a dead run
  becomes a blocked ticket nobody can explain.

So a run carries a **heartbeat**. `running` plus a stale heartbeat is
`abandoned`, and that is a computed answer rather than a state anything has to
remember to write — because whatever kills the process is exactly the thing that
prevents it writing a final status.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Mapping, NewType
from uuid import uuid4

#: Re-exported: `utcnow` is part of this module's published surface, and the
#: store barrel lists it. It lives in `lore_eden.timestamps` so the three
#: modules that once each defined it share one clock.
from lore_eden.timestamps import as_utc, utcnow


class RunStatus(str, Enum):
    """How a run stands.

    ``ABANDONED`` is never written by the run itself — a process that could
    write it would not be abandoned. It is what :meth:`RunRecord.effective_status`
    reports for a ``RUNNING`` row whose heartbeat has gone stale.
    """

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"


#: How long a `running` row may go unheard from before it is presumed dead.
#: Generous, because the cost of the two mistakes is asymmetric: calling a live
#: run abandoned kills work in progress, while waiting a few extra minutes on a
#: dead one costs a few extra minutes.
DEFAULT_STALE_AFTER = timedelta(minutes=15)




@dataclass
class RunRecord:
    """One attempt at one stage: what ran, how long, and how it ended."""

    item_id: str
    stage_key: str
    agent_id: str = ""
    run_id: str = field(default_factory=lambda: uuid4().hex)
    status: RunStatus = RunStatus.RUNNING
    attempt: int = 1
    argv: list[str] = field(default_factory=list)
    #: The agent's own verdict, if it gave one. See `lore_eden.runner.report`.
    outcome: str = ""
    summary: str = ""
    #: Why the harness concluded what it did, when the agent did not say.
    reason: str = ""
    started_at: datetime = field(default_factory=utcnow)
    #: Bumped while the run is alive. Staleness here is what makes it abandoned.
    heartbeat_at: datetime = field(default_factory=utcnow)
    ended_at: datetime | None = None
    #: Whatever the host wants to keep — token counts, a log path, a cost.
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def finished(self) -> bool:
        return self.status is not RunStatus.RUNNING

    def duration(self, now: datetime | None = None) -> timedelta:
        end = self.ended_at or now or utcnow()
        return as_utc(end) - as_utc(self.started_at)

    def effective_status(
        self,
        now: datetime | None = None,
        stale_after: timedelta = DEFAULT_STALE_AFTER,
    ) -> RunStatus:
        """The status, with a stale ``RUNNING`` reported as ``ABANDONED``.

        Read this rather than :attr:`status` anywhere a human or a queue will
        act on the answer. :attr:`status` is what was written; this is what is
        true.
        """
        if self.status is not RunStatus.RUNNING:
            return self.status
        moment = now or utcnow()
        if moment - as_utc(self.heartbeat_at) > stale_after:
            return RunStatus.ABANDONED
        return RunStatus.RUNNING

    def beat(self, now: datetime | None = None) -> "RunRecord":
        return replace(self, heartbeat_at=now or utcnow())

    def finish(
        self,
        status: RunStatus,
        *,
        outcome: str = "",
        summary: str = "",
        reason: str = "",
        now: datetime | None = None,
    ) -> "RunRecord":
        moment = now or utcnow()
        return replace(
            self,
            status=status,
            outcome=outcome,
            summary=summary,
            reason=reason,
            ended_at=moment,
            heartbeat_at=moment,
        )


@dataclass
class CursorRecord:
    """A work item's position, as stored.

    Kept apart from :class:`~lore_eden.workflow.WorkflowCursor`, which is frozen
    and carries no storage concerns. Converting at the boundary means the
    workflow package never learns that a database exists.
    """

    item_id: str
    stage_key: str = ""
    stage_statuses: dict[str, str] = field(default_factory=dict)
    blocking_issues: str = ""
    #: The agent pin for the next run, if one was set. Consumed when read —
    #: see `lore_eden.runner.resolve_agent`.
    agent_override: str = ""
    updated_at: datetime = field(default_factory=utcnow)


#: A work item's state, in whatever vocabulary its host declares.
#:
#: A distinct type rather than a bare ``str``, and not an enum, because both of
#: the obvious answers are wrong here. An enum would have to pick one host's
#: vocabulary — five states in one, six in another, neither a superset — and
#: force the other to translate at every boundary. A bare ``str`` says nothing
#: at all, which is the smell this repo's own gate exists to catch. A ``NewType``
#: names the concept, costs nothing at runtime, and still lets a host bring its
#: own set: it constructs one from its enum's value at the edge, once.
WorkItemState = NewType("WorkItemState", str)


class WorkItemType(str, Enum):
    """The hierarchy a work item sits in.

    Fixed, and deliberately so: the shape of the tree is what the hierarchy
    rules are written against, and a host that could add a level would be
    writing its own rules anyway. What a host *does* vary is the state
    vocabulary — see :class:`WorkItemRecord.state`.
    """

    MILESTONE = "milestone"
    FEATURE = "feature"
    CAPABILITY = "capability"
    TASK = "task"
    BUG = "bug"


#: Which children each type may take. A bug hangs off anything above a task,
#: because a defect is found against the thing it breaks, not against a level.
VALID_CHILDREN: Mapping[WorkItemType, tuple[WorkItemType, ...]] = {
    WorkItemType.MILESTONE: (WorkItemType.FEATURE, WorkItemType.BUG),
    WorkItemType.FEATURE: (WorkItemType.CAPABILITY, WorkItemType.BUG),
    WorkItemType.CAPABILITY: (WorkItemType.TASK, WorkItemType.BUG),
    WorkItemType.TASK: (),
    WorkItemType.BUG: (),
}


@dataclass
class WorkItemRecord:
    """One tracked piece of work, as a plain value.

    ## Why ``state`` is a string and ``item_type`` is not

    The repository these rules come from treats a stringly-typed vocabulary as a
    defect, and its gate says so. This field is the exception, and the reason is
    that the vocabulary is not ours: one host runs five states
    (``backlog, in_progress, blocked, done, wont_do``) and another wants six
    (``BACKLOG -> READY -> ACTIVE -> STALLED -> VALIDATION -> RESOLVED``), and
    neither contains the other. An enum here would force one host to translate
    at every boundary, which is how a vocabulary ends up with two spellings and
    a mapping table nobody trusts.

    So the *storage* layer carries the state opaquely, and the engine validates
    it against the vocabulary its host declares. The type hierarchy is the
    opposite case — the same everywhere the hierarchy rules apply — and stays an
    enum.
    """

    external_id: str
    title: str
    item_type: WorkItemType
    state: WorkItemState
    id: str = field(default_factory=lambda: str(uuid4()))
    parent_id: str = ""
    cycle_id: str = ""
    priority: int = 3
    description: str = ""
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    def touched(self, *, now: datetime | None = None) -> "WorkItemRecord":
        """A copy stamped as changed. Callers do not mutate records in place."""
        return replace(self, updated_at=as_utc(now) if now is not None else utcnow())


@dataclass
class CycleRecord:
    """An iteration — a sprint, by whatever name the host uses.

    ``state`` is host vocabulary for the same reason a work item's is.
    """

    name: str
    state: WorkItemState
    id: str = field(default_factory=lambda: str(uuid4()))
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class RelationKind(str, Enum):
    """How two items relate when neither blocks the other.

    Ours, not the host's: a relation with no defined meaning is a link nobody
    can act on. Ordering lives in the dependency graph instead, where a cycle
    can be refused.
    """

    RELATES_TO = "relates_to"
    DUPLICATES = "duplicates"
    SUPERSEDES = "supersedes"


@dataclass
class RelationRecord:
    """A directed, non-blocking link between two items."""

    item_id: str
    related_id: str
    kind: RelationKind = RelationKind.RELATES_TO

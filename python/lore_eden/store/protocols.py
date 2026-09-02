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
from typing import Protocol, Sequence

from lore_eden.store.records import CursorRecord, RunRecord, RunStatus
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

"""The stores in a dictionary.

Not only a test double. A host running one work item at a time in one process —
a script, a CLI, a scheduled job — needs no database, and making it install one
to use the harness would be a tax on the simplest case.

Records are copied in and out. A store that handed back the object it holds lets
a caller mutate stored state by accident, which is a bug that appears as data
changing with nobody having saved anything.

There is no in-memory approval store here:
:class:`lore_eden.workflow.approvals.ApprovalStore` already is one, and a second
would be a second vocabulary for the same idea.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Sequence

from lore_eden.store.records import CursorRecord, RunRecord, RunStatus, utcnow


class InMemoryCursorStore:
    def __init__(self) -> None:
        self._cursors: dict[str, CursorRecord] = {}

    def get_cursor(self, item_id: str) -> CursorRecord | None:
        found = self._cursors.get(item_id)
        return replace(found) if found is not None else None

    def save_cursor(self, record: CursorRecord) -> CursorRecord:
        # The override is *not* taken from the incoming record. A caller holding
        # a copy read before `take_agent_override` would otherwise write the pin
        # back, and the stale-pin dispatch loop returns. The stored value wins;
        # `set_agent_override` is the only way to write one.
        existing = self._cursors.get(record.item_id)
        stored = replace(
            record,
            agent_override=existing.agent_override if existing is not None else "",
            updated_at=utcnow(),
        )
        self._cursors[record.item_id] = stored
        return replace(stored)

    def set_agent_override(self, item_id: str, agent_id: str) -> None:
        current = self._cursors.get(item_id) or CursorRecord(item_id=item_id)
        self._cursors[item_id] = replace(current, agent_override=agent_id)

    def take_agent_override(self, item_id: str) -> str:
        current = self._cursors.get(item_id)
        if current is None or not current.agent_override:
            return ""
        self._cursors[item_id] = replace(current, agent_override="")
        return current.agent_override

    def list_cursors(self, *, stage_key: str = "") -> Sequence[CursorRecord]:
        return [
            replace(record)
            for record in self._cursors.values()
            if not stage_key or record.stage_key == stage_key
        ]


class InMemoryRunStore:
    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}

    def start_run(self, record: RunRecord) -> RunRecord:
        self._runs[record.run_id] = replace(record)
        return replace(record)

    def get_run(self, run_id: str) -> RunRecord | None:
        found = self._runs.get(run_id)
        return replace(found) if found is not None else None

    def beat(self, run_id: str, *, now: datetime | None = None) -> RunRecord | None:
        current = self._runs.get(run_id)
        if current is None:
            # Silent: a heartbeat for a run that has been cleaned up is not
            # worth raising over, and a caller beating in a loop would have to
            # guard every call.
            return None
        beaten = current.beat(now)
        self._runs[run_id] = beaten
        return replace(beaten)

    def finish_run(self, record: RunRecord) -> RunRecord:
        self._runs[record.run_id] = replace(record)
        return replace(record)

    def list_runs(
        self, *, item_id: str = "", stage_key: str = "", limit: int = 50
    ) -> Sequence[RunRecord]:
        matched = [
            record
            for record in self._runs.values()
            if (not item_id or record.item_id == item_id)
            and (not stage_key or record.stage_key == stage_key)
        ]
        matched.sort(key=lambda record: record.started_at, reverse=True)
        return [replace(record) for record in matched[:limit]]

    def stale_runs(
        self, *, stale_after: timedelta, now: datetime | None = None
    ) -> Sequence[RunRecord]:
        moment = now or utcnow()
        return [
            replace(record)
            for record in self._runs.values()
            if record.effective_status(moment, stale_after) is RunStatus.ABANDONED
        ]

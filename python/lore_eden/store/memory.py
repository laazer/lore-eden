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
from typing import Mapping, Sequence

from lore_eden.ledger import EntityType, LedgerEvent, LedgerSequenceConflict
from lore_eden.usage import UsageRecord
from lore_eden.store.records import (
    CursorRecord,
    CycleRecord,
    RelationRecord,
    RunRecord,
    RunStatus,
    WorkItemRecord,
    RelationKind,
    WorkItemState,
    WorkItemType,
    utcnow,
)


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


class InMemoryWorkItemStore:
    """Items in a dict, keyed by id.

    The child index is derived on read rather than maintained on write. Two
    structures that must agree is a bug waiting for the first caller who
    reparents an item, and this store is small enough that the scan costs less
    than the invariant would.
    """

    def __init__(self) -> None:
        self._items: dict[str, WorkItemRecord] = {}

    def get_item(self, item_id: str) -> WorkItemRecord | None:
        found = self._items.get(item_id)
        return replace(found) if found is not None else None

    def get_items(self, item_ids: Sequence[str]) -> Mapping[str, WorkItemRecord]:
        return {
            item_id: replace(self._items[item_id])
            for item_id in item_ids
            if item_id in self._items
        }

    def save_item(self, record: WorkItemRecord) -> WorkItemRecord:
        stored = replace(record)
        self._items[stored.id] = stored
        return replace(stored)

    def delete_item(self, item_id: str) -> bool:
        return self._items.pop(item_id, None) is not None

    def children_of(self, parent_ids: Sequence[str]) -> Mapping[str, Sequence[WorkItemRecord]]:
        # Every requested key present, including the childless ones: a caller
        # that had to tell "no children" from "not asked" would ask again.
        found: dict[str, list[WorkItemRecord]] = {parent: [] for parent in parent_ids}
        for item in self._items.values():
            if item.parent_id in found:
                found[item.parent_id].append(replace(item))
        return found

    def list_items(
        self,
        *,
        item_type: WorkItemType | None = None,
        state: WorkItemState = WorkItemState(""),
        cycle_id: str = "",
        parent_id: str = "",
        limit: int = 100,
    ) -> Sequence[WorkItemRecord]:
        matched = [
            replace(item)
            for item in self._items.values()
            if (item_type is None or item.item_type == item_type)
            and (not state or item.state == state)
            and (not cycle_id or item.cycle_id == cycle_id)
            and (not parent_id or item.parent_id == parent_id)
        ]
        matched.sort(key=lambda item: (item.priority, item.created_at, item.id))
        return matched[:limit]


class InMemoryDependencyStore:
    """Edges as a set of pairs.

    No cycle check here, deliberately: that needs a walk over the graph, and a
    storage layer that walked would be answering a question the engine owns.
    """

    def __init__(self) -> None:
        self._edges: set[tuple[str, str]] = set()

    def add_dependency(self, item_id: str, depends_on_id: str) -> bool:
        edge = (item_id, depends_on_id)
        if edge in self._edges:
            return False
        self._edges.add(edge)
        return True

    def remove_dependency(self, item_id: str, depends_on_id: str) -> bool:
        edge = (item_id, depends_on_id)
        if edge not in self._edges:
            return False
        self._edges.discard(edge)
        return True

    def prerequisites_map(self, item_ids: Sequence[str]) -> Mapping[str, frozenset[str]]:
        wanted = set(item_ids)
        found: dict[str, set[str]] = {item_id: set() for item_id in wanted}
        for item_id, depends_on_id in self._edges:
            if item_id in wanted:
                found[item_id].add(depends_on_id)
        return {item_id: frozenset(edges) for item_id, edges in found.items()}

    def dependents_map(self, item_ids: Sequence[str]) -> Mapping[str, frozenset[str]]:
        wanted = set(item_ids)
        found: dict[str, set[str]] = {item_id: set() for item_id in wanted}
        for item_id, depends_on_id in self._edges:
            if depends_on_id in wanted:
                found[depends_on_id].add(item_id)
        return {item_id: frozenset(edges) for item_id, edges in found.items()}


class InMemoryCycleStore:
    def __init__(self) -> None:
        self._cycles: dict[str, CycleRecord] = {}

    def get_cycle(self, cycle_id: str) -> CycleRecord | None:
        found = self._cycles.get(cycle_id)
        return replace(found) if found is not None else None

    def save_cycle(self, record: CycleRecord) -> CycleRecord:
        stored = replace(record)
        self._cycles[stored.id] = stored
        return replace(stored)

    def list_cycles(
        self, *, state: WorkItemState = WorkItemState("")
    ) -> Sequence[CycleRecord]:
        return [
            replace(cycle)
            for cycle in self._cycles.values()
            if not state or cycle.state == state
        ]


class InMemoryTagStore:
    def __init__(self) -> None:
        self._tags: dict[str, tuple[str, ...]] = {}

    def tags_for(self, item_ids: Sequence[str]) -> Mapping[str, tuple[str, ...]]:
        return {item_id: self._tags.get(item_id, ()) for item_id in item_ids}

    def set_tags(self, item_id: str, tags: Sequence[str]) -> tuple[str, ...]:
        # Order is the caller's, minus repeats. Sorting would quietly reorder a
        # host's own display order; keeping duplicates would let a tag count
        # twice.
        seen: list[str] = []
        for tag in tags:
            if tag not in seen:
                seen.append(tag)
        stored = tuple(seen)
        self._tags[item_id] = stored
        return stored


class InMemoryRelationStore:
    def __init__(self) -> None:
        self._relations: set[tuple[str, str, str]] = set()

    def add_relation(self, record: RelationRecord) -> bool:
        key = (record.item_id, record.related_id, record.kind.value)
        if key in self._relations:
            return False
        self._relations.add(key)
        return True

    def remove_relation(self, record: RelationRecord) -> bool:
        key = (record.item_id, record.related_id, record.kind.value)
        if key not in self._relations:
            return False
        self._relations.discard(key)
        return True

    def relations_for(
        self, item_ids: Sequence[str]
    ) -> Mapping[str, Sequence[RelationRecord]]:
        wanted = set(item_ids)
        found: dict[str, list[RelationRecord]] = {item_id: [] for item_id in wanted}
        for item_id, related_id, kind in sorted(self._relations):
            if item_id in wanted:
                found[item_id].append(
                    RelationRecord(item_id, related_id, RelationKind(kind))
                )
        return found


class InMemoryLedgerStore:
    """Events in a list, with the same refusals the SQL store makes.

    A test double that accepted what the real store rejects would let a bug
    through the suite and stop it in production, so the uniqueness checks are
    real here too.
    """

    def __init__(self) -> None:
        self._events: list[LedgerEvent] = []

    def last_event(self, entity_id: str, entity_type: EntityType) -> LedgerEvent | None:
        stream = [
            event
            for event in self._events
            if event.entity_id == entity_id and event.entity_type == entity_type
        ]
        if not stream:
            return None
        return max(stream, key=lambda event: event.sequence_number)

    def append_event(self, event: LedgerEvent) -> LedgerEvent:
        taken = any(
            stored.entity_id == event.entity_id
            and stored.entity_type == event.entity_type
            and stored.sequence_number == event.sequence_number
            for stored in self._events
        )
        if taken:
            raise LedgerSequenceConflict(
                f"sequence {event.sequence_number} is already recorded for "
                f"{event.entity_type} '{event.entity_id}'"
            )
        self._events.append(event)
        return event

    def events_for(self, entity_id: str, entity_type: EntityType) -> Sequence[LedgerEvent]:
        stream = [
            event
            for event in self._events
            if event.entity_id == entity_id and event.entity_type == entity_type
        ]
        return sorted(stream, key=lambda event: event.sequence_number)

    def event_by_idempotency_key(self, idempotency_key: str) -> LedgerEvent | None:
        if not idempotency_key:
            return None
        for event in self._events:
            if event.idempotency_key == idempotency_key:
                return event
        return None


class InMemoryUsageStore:
    """Usage entries in a list."""

    def __init__(self) -> None:
        self._records: list[UsageRecord] = []

    def add_usage(self, record: UsageRecord) -> UsageRecord:
        self._records.append(record)
        return record

    def usage_for(
        self,
        group_keys: Sequence[str],
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Mapping[str, Sequence[UsageRecord]]:
        wanted = set(group_keys)
        found: dict[str, list[UsageRecord]] = {group_key: [] for group_key in wanted}
        for record in self._records:
            if record.group_key not in wanted:
                continue
            if since is not None and record.occurred_at < since:
                continue
            if until is not None and record.occurred_at >= until:
                continue
            found[record.group_key].append(record)
        return found

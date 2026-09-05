"""SQLModel implementations of the stores, for a host without its own database.

Optional. `pip install "lore-eden[sql]"` — the protocol layer and the in-memory
stores need none of this, which is checked by a test walking their import graph.

## Foreign keys, and where the PRAGMA goes

SQLite ignores foreign key constraints unless asked, **per connection**. So a
schema declaring them enforces nothing by default, and a test that writes a
child row with a made-up parent id passes — proving the opposite of what it
looks like it proves.

:func:`enforce_sqlite_foreign_keys` registers the PRAGMA on the SQLAlchemy
``Engine`` *class*, so every engine gets it: the application's, each test's, and
a one-off script's. Registering per engine means the one engine somebody forgot
is the one that writes the orphan.

Migrations deliberately run with foreign keys **off**. A table rebuild —
SQLite's only way to alter most things — drops and recreates, and a constraint
enforced mid-rebuild fails against rows that are about to be fine.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta

from sqlalchemy import UniqueConstraint, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, Session, SQLModel, select

from lore_eden.ledger import EntityType, LedgerEvent, LedgerSequenceConflict
from lore_eden.store.records import (
    ANY_STATE,
    CursorRecord,
    CycleRecord,
    RelationKind,
    RelationRecord,
    RunRecord,
    RunStatus,
    WorkItemRecord,
    WorkItemState,
    WorkItemType,
    utcnow,
)
from lore_eden.usage import Measure, UsageRecord

_FK_LISTENER_REGISTERED = False


#: The driver module a stdlib SQLite connection comes from. Compared by name
#: rather than with ``isinstance`` because the object belongs to a driver, not
#: to us, and because importing `sqlite3` to type-check a Postgres connection
#: would be absurd.
SQLITE_DRIVER_MODULE = "sqlite3"


def pragma_foreign_keys_if_sqlite(dbapi_connection: object) -> bool:
    """Turn on FK enforcement, but only on a connection that understands it.

    Returns whether the PRAGMA was issued, so a test can tell "skipped" from
    "ran" without reading the database.

    ## Why the guard exists

    The listener this backs is registered on the ``Engine`` *class*, which is
    what makes it reach every engine a host builds rather than only the ones it
    remembered to decorate. That breadth is the point — and it means a host with
    a Postgres engine got ``PRAGMA foreign_keys=ON`` sent to Postgres, which
    answers ``syntax error at or near "PRAGMA"`` and takes down every connection
    it makes.

    Found by running the store's conformance suite against a real Postgres for
    the first time. Nothing in a SQLite-only test run could have shown it, which
    is the whole argument for testing the second database.
    """
    if type(dbapi_connection).__module__.split(".")[0] != SQLITE_DRIVER_MODULE:
        return False
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
    return True


def enforce_sqlite_foreign_keys() -> None:
    """Turn on FK enforcement for every SQLite engine in this process.

    Idempotent, so a host that also registers it does not end up with two
    listeners firing the same PRAGMA on every connect. Call it once, at import
    of the host's database module. Engines of any other dialect are left alone —
    see :func:`pragma_foreign_keys_if_sqlite`.
    """
    global _FK_LISTENER_REGISTERED
    if _FK_LISTENER_REGISTERED:
        return

    @event.listens_for(Engine, "connect")
    def _pragma(dbapi_connection, connection_record) -> None:  # noqa: ANN001
        pragma_foreign_keys_if_sqlite(dbapi_connection)

    _FK_LISTENER_REGISTERED = True


class CursorRow(SQLModel, table=True):
    __tablename__ = "lore_eden_cursors"

    item_id: str = Field(primary_key=True)
    stage_key: str = ""
    stage_statuses_json: str = "{}"
    blocking_issues: str = ""
    agent_override: str = ""
    updated_at: datetime = Field(default_factory=utcnow)


class RunRow(SQLModel, table=True):
    __tablename__ = "lore_eden_runs"

    run_id: str = Field(primary_key=True)
    # Indexed because "what happened to this item?" is the query a UI makes on
    # every page load, and a table scan for it grows with total history.
    item_id: str = Field(index=True)
    stage_key: str = Field(index=True)
    agent_id: str = ""
    status: str = RunStatus.RUNNING.value
    attempt: int = 1
    argv_json: str = "[]"
    outcome: str = ""
    summary: str = ""
    reason: str = ""
    started_at: datetime = Field(default_factory=utcnow)
    # Indexed: the stale-run sweep filters on it, and that sweep runs on a timer
    # against every row that ever ran.
    heartbeat_at: datetime = Field(default_factory=utcnow, index=True)
    ended_at: datetime | None = None
    metadata_json: str = "{}"


def _to_run(row: RunRow) -> RunRecord:
    return RunRecord(
        item_id=row.item_id,
        stage_key=row.stage_key,
        agent_id=row.agent_id,
        run_id=row.run_id,
        status=RunStatus(row.status),
        attempt=row.attempt,
        argv=json.loads(row.argv_json),
        outcome=row.outcome,
        summary=row.summary,
        reason=row.reason,
        started_at=row.started_at,
        heartbeat_at=row.heartbeat_at,
        ended_at=row.ended_at,
        metadata=json.loads(row.metadata_json),
    )


def _apply_run(row: RunRow, record: RunRecord) -> RunRow:
    row.item_id = record.item_id
    row.stage_key = record.stage_key
    row.agent_id = record.agent_id
    row.status = record.status.value
    row.attempt = record.attempt
    row.argv_json = json.dumps(record.argv)
    row.outcome = record.outcome
    row.summary = record.summary
    row.reason = record.reason
    row.started_at = record.started_at
    row.heartbeat_at = record.heartbeat_at
    row.ended_at = record.ended_at
    row.metadata_json = json.dumps(dict(record.metadata))
    return row


class SqlCursorStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_cursor(self, item_id: str) -> CursorRecord | None:
        row = self.session.get(CursorRow, item_id)
        if row is None:
            return None
        return CursorRecord(
            item_id=row.item_id,
            stage_key=row.stage_key,
            stage_statuses=json.loads(row.stage_statuses_json),
            blocking_issues=row.blocking_issues,
            agent_override=row.agent_override,
            updated_at=row.updated_at,
        )

    def save_cursor(self, record: CursorRecord) -> CursorRecord:
        row = self.session.get(CursorRow, record.item_id) or CursorRow(
            item_id=record.item_id
        )
        row.stage_key = record.stage_key
        row.stage_statuses_json = json.dumps(record.stage_statuses)
        row.blocking_issues = record.blocking_issues
        # Not written here: `save_cursor` must not resurrect a pin that
        # `take_agent_override` just consumed, and a caller holding a record
        # read before the take would do exactly that.
        row.updated_at = utcnow()
        self.session.add(row)
        self.session.commit()
        return replace(record, updated_at=row.updated_at)

    def set_agent_override(self, item_id: str, agent_id: str) -> None:
        row = self.session.get(CursorRow, item_id) or CursorRow(item_id=item_id)
        row.agent_override = agent_id
        self.session.add(row)
        self.session.commit()

    def take_agent_override(self, item_id: str) -> str:
        row = self.session.get(CursorRow, item_id)
        if row is None or not row.agent_override:
            return ""
        pinned = row.agent_override
        row.agent_override = ""
        self.session.add(row)
        self.session.commit()
        return pinned

    def list_cursors(self, *, stage_key: str = "") -> Sequence[CursorRecord]:
        statement = select(CursorRow)
        if stage_key:
            statement = statement.where(CursorRow.stage_key == stage_key)
        found = self.session.exec(statement).all()
        return [self.get_cursor(row.item_id) for row in found]  # type: ignore[misc]


class SqlRunStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def start_run(self, record: RunRecord) -> RunRecord:
        row = _apply_run(RunRow(run_id=record.run_id), record)
        self.session.add(row)
        self.session.commit()
        return replace(record)

    def get_run(self, run_id: str) -> RunRecord | None:
        row = self.session.get(RunRow, run_id)
        return _to_run(row) if row is not None else None

    def beat(self, run_id: str, *, now: datetime | None = None) -> RunRecord | None:
        row = self.session.get(RunRow, run_id)
        if row is None:
            return None
        row.heartbeat_at = now or utcnow()
        self.session.add(row)
        self.session.commit()
        return _to_run(row)

    def finish_run(self, record: RunRecord) -> RunRecord:
        row = self.session.get(RunRow, record.run_id) or RunRow(run_id=record.run_id)
        _apply_run(row, record)
        self.session.add(row)
        self.session.commit()
        return replace(record)

    def list_runs(
        self, *, item_id: str = "", stage_key: str = "", limit: int = 50
    ) -> Sequence[RunRecord]:
        statement = select(RunRow)
        if item_id:
            statement = statement.where(RunRow.item_id == item_id)
        if stage_key:
            statement = statement.where(RunRow.stage_key == stage_key)
        statement = statement.order_by(RunRow.started_at.desc()).limit(limit)  # type: ignore[attr-defined]
        return [_to_run(row) for row in self.session.exec(statement).all()]

    def stale_runs(
        self, *, stale_after: timedelta, now: datetime | None = None
    ) -> Sequence[RunRecord]:
        moment = now or utcnow()
        statement = select(RunRow).where(RunRow.status == RunStatus.RUNNING.value)
        # Filtered in Python rather than SQL: SQLite stores naive datetimes and
        # comparing an aware cutoff against them in the database silently
        # compares strings. `effective_status` already normalizes.
        return [
            record
            for record in (_to_run(row) for row in self.session.exec(statement).all())
            if record.effective_status(moment, stale_after) is RunStatus.ABANDONED
        ]


# --- async, for a host whose whole stack is async -----------------------------
#
# `corpocoin` and `bridgepath` both wire this, and their two files differ by one
# variable name and two optional pool keyword arguments. The one difference was
# my stated reason for declining to share them, which was wrong: pool sizes are
# arguments, not a reason.
#
# The deeper gap it hid: everything above is *sync* SQLAlchemy, so this store
# could not serve either backend at all.


def make_async_engine(
    url: str,
    *,
    echo: bool = False,
    pool_size: int | None = None,
    max_overflow: int | None = None,
):
    """An async engine with the settings both backends chose identically.

    ``pool_pre_ping`` is on in both and is the one worth keeping without being
    asked: a pooled connection that a database restart or an idle timeout has
    killed looks fine until it is used, and the failure surfaces as one
    arbitrary request dying rather than as anything about the pool.

    ``pool_size`` and ``max_overflow`` are passed through only when given, so
    SQLAlchemy's defaults stay the defaults rather than this function inventing
    numbers for a host it knows nothing about.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    options: dict[str, object] = {"echo": echo, "pool_pre_ping": True}
    if pool_size is not None:
        options["pool_size"] = pool_size
    if max_overflow is not None:
        options["max_overflow"] = max_overflow
    return create_async_engine(url, **options)


def make_async_session_factory(engine):
    """A session factory with ``expire_on_commit=False``.

    Both backends set that flag and neither said why, so: without it, every
    attribute of a committed object is expired, and the next read of one issues
    a fresh query — which in an async session raises rather than lazily loading.
    So returning a just-committed row from a request handler blows up, and the
    fix looks like a mysterious `MissingGreenlet`. Off is the right default for
    a request-scoped session, and it is worth carrying with the reason attached.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# The work-item engine's tables.
#
# Everything below goes through SQLModel's expression API rather than text SQL,
# which is what makes it portable: the same store runs on SQLite for a host that
# wants no server and on Postgres for one that has one, and CI proves both
# rather than assuming. The one dialect-specific thing in this module is the
# SQLite foreign-key PRAGMA above, which Postgres does not need because it never
# had the problem.
# ---------------------------------------------------------------------------


class WorkItemRow(SQLModel, table=True):
    __tablename__ = "lore_eden_work_items"

    id: str = Field(primary_key=True)
    # Indexed: a host addresses items by the id its people type, not the UUID.
    external_id: str = Field(index=True)
    title: str = ""
    item_type: str = WorkItemType.TASK.value
    # Not indexed together with parent_id as a composite: the two are filtered
    # independently far more often than jointly, and a composite would serve
    # neither.
    state: str = Field(default="", index=True)
    parent_id: str = Field(default="", index=True)
    cycle_id: str = Field(default="", index=True)
    priority: int = 3
    description: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class CycleRow(SQLModel, table=True):
    __tablename__ = "lore_eden_cycles"

    id: str = Field(primary_key=True)
    name: str = ""
    state: str = Field(default="", index=True)
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class DependencyRow(SQLModel, table=True):
    """One edge. Composite primary key, so re-adding one cannot duplicate it."""

    __tablename__ = "lore_eden_work_item_dependencies"

    item_id: str = Field(primary_key=True)
    # Indexed as well as keyed: the primary key serves "what does this wait
    # for", and the reverse question — "what waits on this" — is asked by every
    # attempt to close or reorder an item.
    depends_on_id: str = Field(primary_key=True, index=True)


class TagRow(SQLModel, table=True):
    __tablename__ = "lore_eden_work_item_tags"

    item_id: str = Field(primary_key=True)
    tag: str = Field(primary_key=True)
    # Tags are returned in the order the caller supplied, so the order has to be
    # stored. A set would come back alphabetised, silently reordering a host's
    # own display.
    position: int = 0


class RelationRow(SQLModel, table=True):
    __tablename__ = "lore_eden_work_item_relations"

    item_id: str = Field(primary_key=True)
    related_id: str = Field(primary_key=True)
    kind: str = Field(primary_key=True, default=RelationKind.RELATES_TO.value)


def _to_item(row: WorkItemRow) -> WorkItemRecord:
    return WorkItemRecord(
        id=row.id,
        external_id=row.external_id,
        title=row.title,
        item_type=WorkItemType(row.item_type),
        state=row.state,
        parent_id=row.parent_id,
        cycle_id=row.cycle_id,
        priority=row.priority,
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _apply_item(row: WorkItemRow, record: WorkItemRecord) -> WorkItemRow:
    row.external_id = record.external_id
    row.title = record.title
    row.item_type = record.item_type.value
    row.state = record.state
    row.parent_id = record.parent_id
    row.cycle_id = record.cycle_id
    row.priority = record.priority
    row.description = record.description
    row.created_at = record.created_at
    row.updated_at = record.updated_at
    return row


class SqlWorkItemStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_item(self, item_id: str) -> WorkItemRecord | None:
        row = self.session.get(WorkItemRow, item_id)
        return _to_item(row) if row is not None else None

    def get_items(self, item_ids: Sequence[str]) -> Mapping[str, WorkItemRecord]:
        if not item_ids:
            return {}
        rows = self.session.exec(
            select(WorkItemRow).where(WorkItemRow.id.in_(list(item_ids)))
        ).all()
        return {row.id: _to_item(row) for row in rows}

    def save_item(self, record: WorkItemRecord) -> WorkItemRecord:
        row = self.session.get(WorkItemRow, record.id) or WorkItemRow(id=record.id)
        self.session.add(_apply_item(row, record))
        self.session.commit()
        self.session.refresh(row)
        return _to_item(row)

    def delete_item(self, item_id: str) -> bool:
        row = self.session.get(WorkItemRow, item_id)
        if row is None:
            return False
        self.session.delete(row)
        self.session.commit()
        return True

    def children_of(self, parent_ids: Sequence[str]) -> Mapping[str, Sequence[WorkItemRecord]]:
        found: dict[str, list[WorkItemRecord]] = {parent: [] for parent in parent_ids}
        if not parent_ids:
            return found
        # One query for every parent asked about. The loop that would have been
        # here instead is the N+1 a board of two hundred cards cannot afford.
        rows = self.session.exec(
            select(WorkItemRow).where(WorkItemRow.parent_id.in_(list(parent_ids)))
        ).all()
        for row in rows:
            found[row.parent_id].append(_to_item(row))
        return found

    def list_items(
        self,
        *,
        item_type: WorkItemType | None = None,
        state: WorkItemState = ANY_STATE,
        cycle_id: str = "",
        parent_id: str = "",
        limit: int = 100,
    ) -> Sequence[WorkItemRecord]:
        statement = select(WorkItemRow)
        if item_type is not None:
            statement = statement.where(WorkItemRow.item_type == item_type.value)
        if state:
            statement = statement.where(WorkItemRow.state == state)
        if cycle_id:
            statement = statement.where(WorkItemRow.cycle_id == cycle_id)
        if parent_id:
            statement = statement.where(WorkItemRow.parent_id == parent_id)
        statement = statement.order_by(
            WorkItemRow.priority, WorkItemRow.created_at, WorkItemRow.id
        ).limit(limit)
        return [_to_item(row) for row in self.session.exec(statement).all()]


class SqlDependencyStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_dependency(self, item_id: str, depends_on_id: str) -> bool:
        existing = self.session.get(DependencyRow, (item_id, depends_on_id))
        if existing is not None:
            return False
        self.session.add(DependencyRow(item_id=item_id, depends_on_id=depends_on_id))
        self.session.commit()
        return True

    def remove_dependency(self, item_id: str, depends_on_id: str) -> bool:
        row = self.session.get(DependencyRow, (item_id, depends_on_id))
        if row is None:
            return False
        self.session.delete(row)
        self.session.commit()
        return True

    def prerequisites_map(self, item_ids: Sequence[str]) -> Mapping[str, frozenset[str]]:
        found: dict[str, set[str]] = {item_id: set() for item_id in item_ids}
        if not item_ids:
            return {}
        rows = self.session.exec(
            select(DependencyRow).where(DependencyRow.item_id.in_(list(item_ids)))
        ).all()
        for row in rows:
            found[row.item_id].add(row.depends_on_id)
        return {item_id: frozenset(edges) for item_id, edges in found.items()}

    def dependents_map(self, item_ids: Sequence[str]) -> Mapping[str, frozenset[str]]:
        found: dict[str, set[str]] = {item_id: set() for item_id in item_ids}
        if not item_ids:
            return {}
        rows = self.session.exec(
            select(DependencyRow).where(DependencyRow.depends_on_id.in_(list(item_ids)))
        ).all()
        for row in rows:
            found[row.depends_on_id].add(row.item_id)
        return {item_id: frozenset(edges) for item_id, edges in found.items()}


class SqlCycleStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_cycle(self, cycle_id: str) -> CycleRecord | None:
        row = self.session.get(CycleRow, cycle_id)
        if row is None:
            return None
        return CycleRecord(
            id=row.id,
            name=row.name,
            state=row.state,
            starts_at=row.starts_at,
            ends_at=row.ends_at,
        )

    def save_cycle(self, record: CycleRecord) -> CycleRecord:
        row = self.session.get(CycleRow, record.id) or CycleRow(id=record.id)
        row.name = record.name
        row.state = record.state
        row.starts_at = record.starts_at
        row.ends_at = record.ends_at
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return replace(record)

    def list_cycles(
        self, *, state: WorkItemState = ANY_STATE
    ) -> Sequence[CycleRecord]:
        statement = select(CycleRow)
        if state:
            statement = statement.where(CycleRow.state == state)
        return [
            CycleRecord(
                id=row.id,
                name=row.name,
                state=row.state,
                starts_at=row.starts_at,
                ends_at=row.ends_at,
            )
            for row in self.session.exec(statement).all()
        ]


class SqlTagStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def tags_for(self, item_ids: Sequence[str]) -> Mapping[str, tuple[str, ...]]:
        found: dict[str, list[tuple[int, str]]] = {item_id: [] for item_id in item_ids}
        if not item_ids:
            return {}
        rows = self.session.exec(
            select(TagRow).where(TagRow.item_id.in_(list(item_ids)))
        ).all()
        for row in rows:
            found[row.item_id].append((row.position, row.tag))
        return {
            item_id: tuple(tag for _, tag in sorted(pairs))
            for item_id, pairs in found.items()
        }

    def set_tags(self, item_id: str, tags: Sequence[str]) -> tuple[str, ...]:
        for row in self.session.exec(
            select(TagRow).where(TagRow.item_id == item_id)
        ).all():
            self.session.delete(row)
        ordered: list[str] = []
        for tag in tags:
            if tag not in ordered:
                ordered.append(tag)
        for position, tag in enumerate(ordered):
            self.session.add(TagRow(item_id=item_id, tag=tag, position=position))
        self.session.commit()
        return tuple(ordered)


class SqlRelationStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _key(self, record: RelationRecord) -> tuple[str, str, str]:
        return (record.item_id, record.related_id, record.kind.value)

    def add_relation(self, record: RelationRecord) -> bool:
        if self.session.get(RelationRow, self._key(record)) is not None:
            return False
        self.session.add(
            RelationRow(
                item_id=record.item_id,
                related_id=record.related_id,
                kind=record.kind.value,
            )
        )
        self.session.commit()
        return True

    def remove_relation(self, record: RelationRecord) -> bool:
        row = self.session.get(RelationRow, self._key(record))
        if row is None:
            return False
        self.session.delete(row)
        self.session.commit()
        return True

    def relations_for(
        self, item_ids: Sequence[str]
    ) -> Mapping[str, Sequence[RelationRecord]]:
        found: dict[str, list[RelationRecord]] = {item_id: [] for item_id in item_ids}
        if not item_ids:
            return {}
        rows = self.session.exec(
            select(RelationRow)
            .where(RelationRow.item_id.in_(list(item_ids)))
            .order_by(RelationRow.related_id, RelationRow.kind)
        ).all()
        for row in rows:
            found[row.item_id].append(
                RelationRecord(row.item_id, row.related_id, RelationKind(row.kind))
            )
        return found


class LedgerEventRow(SQLModel, table=True):
    """One recorded event.

    The uniqueness constraint on ``(entity_id, entity_type, sequence_number)``
    is what makes concurrent appends safe on a backend that cannot lock: two
    racers compute the same sequence number, one insert wins, and the loser gets
    an IntegrityError it can retry from. It is the guarantee, not a tidiness
    index — dropping it would leave a ledger that renumbers itself under load.
    """

    __tablename__ = "lore_eden_ledger_events"
    __table_args__ = (
        UniqueConstraint(
            "entity_id", "entity_type", "sequence_number", name="uq_lore_eden_ledger_sequence"
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    event_type: str = ""
    entity_id: str = Field(index=True)
    entity_type: str = Field(index=True)
    actor: str = ""
    payload_json: str = "{}"
    sequence_number: int = 0
    checksum: str = ""
    # NULL rather than "" for an absent key, because the uniqueness constraint
    # has to permit any number of events that carry no key at all, and SQL
    # treats NULLs as distinct while it treats empty strings as equal.
    idempotency_key: str | None = Field(default=None, unique=True, index=True)
    occurred_at: datetime = Field(default_factory=utcnow)


def _to_ledger_event(row: LedgerEventRow) -> LedgerEvent:
    return LedgerEvent(
        event_type=row.event_type,
        entity_id=row.entity_id,
        entity_type=row.entity_type,
        payload=json.loads(row.payload_json),
        sequence_number=row.sequence_number,
        checksum=row.checksum,
        actor=row.actor,
        idempotency_key=row.idempotency_key or "",
        occurred_at=row.occurred_at,
    )


class SqlLedgerStore:
    """The ledger on SQLModel.

    No update and no delete, matching the protocol. A host that wants to remove
    history has to go around this class, which is the point: the code cannot be
    read as offering it.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def last_event(self, entity_id: str, entity_type: EntityType) -> LedgerEvent | None:
        # `with_for_update` serializes concurrent appends on a backend that has
        # row locks. SQLite has none and SQLAlchemy renders nothing for it there,
        # which is why the uniqueness constraint and the caller's retry are the
        # real guarantee rather than a belt to this braces.
        statement = (
            select(LedgerEventRow)
            .where(
                LedgerEventRow.entity_id == entity_id,
                LedgerEventRow.entity_type == entity_type,
            )
            .order_by(LedgerEventRow.sequence_number.desc())
            .limit(1)
            .with_for_update()
        )
        row = self.session.exec(statement).first()
        return _to_ledger_event(row) if row is not None else None

    def append_event(self, event: LedgerEvent) -> LedgerEvent:
        row = LedgerEventRow(
            event_type=str(event.event_type),
            entity_id=event.entity_id,
            entity_type=str(event.entity_type),
            actor=event.actor,
            payload_json=json.dumps(dict(event.payload), sort_keys=True),
            sequence_number=event.sequence_number,
            checksum=event.checksum,
            idempotency_key=event.idempotency_key or None,
            occurred_at=event.occurred_at,
        )
        self.session.add(row)
        try:
            self.session.commit()
        except IntegrityError as exc:
            # The sequence was taken between this caller's read and its write,
            # or the key was. Roll back so the session is usable, and say which
            # kind of refusal this was rather than leaking the driver's message.
            self.session.rollback()
            raise LedgerSequenceConflict(
                f"sequence {event.sequence_number} is already recorded for "
                f"{event.entity_type} '{event.entity_id}'"
            ) from exc
        self.session.refresh(row)
        return _to_ledger_event(row)

    def events_for(self, entity_id: str, entity_type: EntityType) -> Sequence[LedgerEvent]:
        statement = (
            select(LedgerEventRow)
            .where(
                LedgerEventRow.entity_id == entity_id,
                LedgerEventRow.entity_type == entity_type,
            )
            .order_by(LedgerEventRow.sequence_number)
        )
        return [_to_ledger_event(row) for row in self.session.exec(statement).all()]

    def event_by_idempotency_key(self, idempotency_key: str) -> LedgerEvent | None:
        if not idempotency_key:
            return None
        statement = select(LedgerEventRow).where(
            LedgerEventRow.idempotency_key == idempotency_key
        )
        row = self.session.exec(statement).first()
        return _to_ledger_event(row) if row is not None else None


class UsageRow(SQLModel, table=True):
    """One recorded spend.

    ``amounts_json`` rather than columns per measure: one host counts four token
    figures and another counts money, and a schema migration per measure a host
    invents is a library asking to be forked.
    """

    __tablename__ = "lore_eden_usage"

    id: int | None = Field(default=None, primary_key=True)
    subject_id: str = Field(index=True)
    group_key: str = Field(index=True)
    amounts_json: str = "{}"
    # Indexed because every cost question has a window in it, and a scan over
    # all history to answer "last month" grows with the history rather than the
    # month.
    occurred_at: datetime = Field(default_factory=utcnow, index=True)


def _to_usage(row: UsageRow) -> UsageRecord:
    stored = json.loads(row.amounts_json)
    return UsageRecord(
        subject_id=row.subject_id,
        group_key=row.group_key,
        amounts={Measure(name): value for name, value in stored.items()},
        occurred_at=row.occurred_at,
    )


class SqlUsageStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_usage(self, record: UsageRecord) -> UsageRecord:
        row = UsageRow(
            subject_id=record.subject_id,
            group_key=record.group_key,
            amounts_json=json.dumps(
                {str(measure): value for measure, value in record.amounts.items()},
                sort_keys=True,
            ),
            occurred_at=record.occurred_at,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return _to_usage(row)

    def usage_for(
        self,
        group_keys: Sequence[str],
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Mapping[str, Sequence[UsageRecord]]:
        found: dict[str, list[UsageRecord]] = {group_key: [] for group_key in group_keys}
        if not group_keys:
            return {}
        statement = select(UsageRow).where(UsageRow.group_key.in_(list(group_keys)))
        if since is not None:
            statement = statement.where(UsageRow.occurred_at >= since)
        if until is not None:
            statement = statement.where(UsageRow.occurred_at < until)
        for row in self.session.exec(statement.order_by(UsageRow.occurred_at)).all():
            found[row.group_key].append(_to_usage(row))
        return found

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
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Sequence

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, select

from lore_eden.store.records import CursorRecord, RunRecord, RunStatus, utcnow


_FK_LISTENER_REGISTERED = False


def enforce_sqlite_foreign_keys() -> None:
    """Turn on FK enforcement for every SQLite engine in this process.

    Idempotent, so a host that also registers it does not end up with two
    listeners firing the same PRAGMA on every connect. Call it once, at import
    of the host's database module.
    """
    global _FK_LISTENER_REGISTERED
    if _FK_LISTENER_REGISTERED:
        return

    @event.listens_for(Engine, "connect")
    def _pragma(dbapi_connection, connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

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

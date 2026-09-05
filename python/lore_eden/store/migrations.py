"""Schema changes, applied in order and never rewritten.

## Append-only, and why that is not bureaucracy

A migration that has run somewhere cannot be edited, because the database it ran
against will not run it again. Editing one produces two schemas that both claim
to be at the same version — and the difference surfaces later, somewhere else,
as a column that exists in development and not in production.

So: add a new entry, never change an applied one. Each migration guards its own
changes and is safe to re-run, since the only reliable way to know whether one
applied is to check the schema rather than trust a version number.

Foreign keys are **off** while these run, via :func:`foreign_keys_disabled`.
SQLite alters most things by rebuilding the table — create, copy, drop, rename —
and with enforcement on, the ``DROP`` fails outright against any table that
references the one being rebuilt.

That sentence was in this docstring before anything implemented it, and
``PRAGMA foreign_keys`` is silently ignored inside a transaction, so getting it
wrong looks exactly like getting it right. Both are covered by tests that assert
the pragma's value rather than trusting the statement to have worked.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator, Sequence

from sqlalchemy import text
from sqlmodel import Session

from lore_eden.store.migration_utils import table_columns, table_exists


@dataclass(frozen=True)
class Migration:
    """One schema change, identified by an id that never changes."""

    migration_id: str
    description: str
    apply: Callable[[Session], None]




def _0001_create_cursors_and_runs(session: Session) -> None:
    # The tables themselves come from SQLModel metadata via `create_all`, which
    # is what a fresh install runs. This migration exists so an install that
    # predates the tables reaches the same place, and so the id is claimed.
    from lore_eden.store.sql import CursorRow, RunRow  # noqa: F401

    SQLModelBase = CursorRow.metadata
    SQLModelBase.create_all(
        session.get_bind(), tables=[CursorRow.__table__, RunRow.__table__]
    )


def _0002_index_run_heartbeat(session: Session) -> None:
    # The stale-run sweep filters on heartbeat_at and runs on a timer against
    # every row that ever ran.
    if not table_exists(session, "lore_eden_runs"):
        return
    session.exec(  # type: ignore[call-overload]
        text(
            "CREATE INDEX IF NOT EXISTS ix_lore_eden_runs_heartbeat_at "
            "ON lore_eden_runs (heartbeat_at)"
        )
    )
    session.commit()


def _0003_runs_carry_an_attempt(session: Session) -> None:
    """Backfill for a database created before attempts were counted.

    Guarded on the column's absence rather than on a version, because that is
    the only check that is true whichever path the database took to get here.
    """
    if not table_exists(session, "lore_eden_runs"):
        return
    if "attempt" in table_columns(session, "lore_eden_runs"):
        return
    session.exec(  # type: ignore[call-overload]
        text("ALTER TABLE lore_eden_runs ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1")
    )
    session.commit()


def _0004_create_work_item_tables(session: Session) -> None:
    """The work-item engine's five tables.

    Guarded the same way 0001 is, and for the same reason: a fresh install gets
    these from ``create_all``, so this exists to bring an older database to the
    same place and to claim the id. ``create_all`` is itself the guard — it
    creates only what is missing, which is what makes re-running safe.
    """
    from lore_eden.store.sql import (  # noqa: F401
        CycleRow,
        DependencyRow,
        RelationRow,
        TagRow,
        WorkItemRow,
    )

    WorkItemRow.metadata.create_all(
        session.get_bind(),
        tables=[
            WorkItemRow.__table__,
            CycleRow.__table__,
            DependencyRow.__table__,
            TagRow.__table__,
            RelationRow.__table__,
        ],
    )


def _0005_create_ledger_table(session: Session) -> None:
    """The append-only ledger's one table, with its sequence constraint.

    Guarded by ``create_all``, which creates only what is missing. The
    uniqueness constraint arrives with the table rather than as a later
    migration on purpose: a ledger that ran for a while without it may already
    hold the duplicate rows the constraint would refuse, and there is no
    honest repair for that — the rows cannot be deleted.
    """
    from lore_eden.store.sql import LedgerEventRow  # noqa: F401

    LedgerEventRow.metadata.create_all(
        session.get_bind(), tables=[LedgerEventRow.__table__]
    )


def _0006_create_usage_table(session: Session) -> None:
    """The usage table. Guarded by ``create_all``, which creates only what is
    missing."""
    from lore_eden.store.sql import UsageRow  # noqa: F401

    UsageRow.metadata.create_all(session.get_bind(), tables=[UsageRow.__table__])


#: In order. Append; never edit an entry that has shipped.
MIGRATIONS: tuple[Migration, ...] = (
    Migration("0001", "Create the cursor and run tables", _0001_create_cursors_and_runs),
    Migration("0002", "Index run heartbeats for the stale sweep", _0002_index_run_heartbeat),
    Migration("0003", "Runs carry an attempt number", _0003_runs_carry_an_attempt),
    Migration("0004", "Create the work-item tables", _0004_create_work_item_tables),
    Migration("0005", "Create the ledger table", _0005_create_ledger_table),
    Migration("0006", "Create the usage table", _0006_create_usage_table),
)


@contextmanager
def foreign_keys_disabled(session: Session) -> Iterator[None]:
    """Turn SQLite's foreign-key enforcement off for the duration, then restore it.

    SQLite alters most things by rebuilding the table — create, copy, drop,
    rename — and with enforcement on, the ``DROP`` fails against any table that
    references the one being rebuilt. So a rebuild migration is impossible
    unless this runs first.

    Two details this depends on, both verified rather than assumed:

    - ``PRAGMA foreign_keys`` is a **no-op inside a transaction**, so the
      session is committed before the pragma is issued. Without that commit the
      statement succeeds and changes nothing, which is the worst outcome
      available: enforcement stays on and the caller believes it is off.
    - The prior value is read back and restored, rather than assuming ``ON``. A
      host that runs with enforcement off should not have it switched on by
      applying a migration.

    A non-SQLite backend has no such pragma, and enforcement there is not
    something a migration can toggle per-connection, so this is a no-op.
    """
    bind = session.get_bind()
    if bind.dialect.name != "sqlite":
        yield
        return

    session.commit()
    previous = session.execute(text("PRAGMA foreign_keys")).scalar()
    session.execute(text("PRAGMA foreign_keys=OFF"))
    try:
        yield
    finally:
        session.commit()
        session.execute(text(f"PRAGMA foreign_keys={'ON' if previous else 'OFF'}"))


def run_migrations(session: Session, migrations: Sequence[Migration] = MIGRATIONS) -> list[str]:
    """Apply every migration in order, with foreign keys off. Returns the ids that ran.

    Every migration is re-run on every call, which is safe because each guards
    its own changes — and is the reason no ledger table is needed. A ledger is
    one more thing that can disagree with the schema it describes.

    Enforcement is disabled for the whole run rather than per-migration, because
    a rebuild leaves referencing tables briefly pointing at a table that does
    not exist, and that window can span two migrations.
    """
    applied: list[str] = []
    with foreign_keys_disabled(session):
        for migration in migrations:
            migration.apply(session)
            applied.append(migration.migration_id)
    return applied

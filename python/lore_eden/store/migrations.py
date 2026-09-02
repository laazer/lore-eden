"""Schema changes, applied in order and never rewritten.

## Append-only, and why that is not bureaucracy

A migration that has run somewhere cannot be edited, because the database it ran
against will not run it again. Editing one produces two schemas that both claim
to be at the same version — and the difference surfaces later, somewhere else,
as a column that exists in development and not in production.

So: add a new entry, never change an applied one. Each migration guards its own
changes and is safe to re-run, since the only reliable way to know whether one
applied is to check the schema rather than trust a version number.

Foreign keys are **off** while these run. SQLite alters most things by rebuilding
the table — create, copy, drop, rename — and a constraint enforced mid-rebuild
fails against rows that are about to be fine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from sqlalchemy import inspect, text
from sqlmodel import Session


@dataclass(frozen=True)
class Migration:
    """One schema change, identified by an id that never changes."""

    migration_id: str
    description: str
    apply: Callable[[Session], None]


def _has_table(session: Session, table: str) -> bool:
    return inspect(session.get_bind()).has_table(table)


def _columns(session: Session, table: str) -> set[str]:
    if not _has_table(session, table):
        return set()
    return {column["name"] for column in inspect(session.get_bind()).get_columns(table)}


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
    if not _has_table(session, "lore_eden_runs"):
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
    if not _has_table(session, "lore_eden_runs"):
        return
    if "attempt" in _columns(session, "lore_eden_runs"):
        return
    session.exec(  # type: ignore[call-overload]
        text("ALTER TABLE lore_eden_runs ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1")
    )
    session.commit()


#: In order. Append; never edit an entry that has shipped.
MIGRATIONS: tuple[Migration, ...] = (
    Migration("0001", "Create the cursor and run tables", _0001_create_cursors_and_runs),
    Migration("0002", "Index run heartbeats for the stale sweep", _0002_index_run_heartbeat),
    Migration("0003", "Runs carry an attempt number", _0003_runs_carry_an_attempt),
)


def run_migrations(session: Session, migrations: Sequence[Migration] = MIGRATIONS) -> list[str]:
    """Apply every migration in order. Returns the ids that ran.

    Every migration is re-run on every call, which is safe because each guards
    its own changes — and is the reason no ledger table is needed. A ledger is
    one more thing that can disagree with the schema it describes.
    """
    applied: list[str] = []
    for migration in migrations:
        migration.apply(session)
        applied.append(migration.migration_id)
    return applied

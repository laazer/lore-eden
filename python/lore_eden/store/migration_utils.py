"""Schema introspection for migrations that guard their own changes.

:mod:`lore_eden.store.migrations` asks every migration to be safe to re-run,
because the only reliable way to know whether one applied is to look at the
schema rather than trust a version number. That doctrine needs helpers, or each
migration hand-writes its own guard and they drift.

Everything here is a question about the current schema (:func:`table_exists`,
:func:`column_is_nullable`) or an idempotent change (:func:`add_columns_if_missing`,
:func:`relax_not_null`) — safe to call whether or not the change has already
been made.

SQLite-specific on purpose. The introspection could go through SQLAlchemy's
``inspect``, but :func:`relax_not_null` could not: SQLite has no
``ALTER COLUMN``, and the table rebuild it needs has no portable spelling.
"""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import text
from sqlmodel import Session


def table_exists(session: Session, table: str) -> bool:
    row = session.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:name"),
        {"name": table},
    ).fetchone()
    return row is not None


def index_exists(session: Session, name: str) -> bool:
    row = session.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='index' AND name=:name"),
        {"name": name},
    ).fetchone()
    return row is not None


def table_columns(session: Session, table: str) -> set[str]:
    """Column names on ``table``, or an empty set if it does not exist."""
    return {row[1] for row in _table_info(session, table)}


def column_is_nullable(session: Session, table: str, column: str) -> bool:
    """Whether ``column`` accepts NULL. An absent column reads as nullable.

    That default keeps :func:`relax_not_null` a no-op on a table it has already
    rebuilt, and on a column a later migration removed.
    """
    for row in _table_info(session, table):
        if row[1] == column:
            return not row[3]
    return True


def add_columns_if_missing(session: Session, table: str, columns: dict[str, str]) -> None:
    """Run each ``column name -> ALTER statement`` whose column is absent.

    Absent table means nothing to do: an install that never created it will get
    it from the migration that does, with the column already in place.
    """
    if not table_exists(session, table):
        return
    existing = table_columns(session, table)
    for name, statement in columns.items():
        if name not in existing:
            session.execute(text(statement))


def relax_not_null(session: Session, table: str, column: str) -> None:
    """Drop the NOT NULL constraint on one column by rebuilding the table.

    SQLite has no ``ALTER COLUMN``, so the table is recreated and copied. The
    replacement schema is read back from ``PRAGMA`` rather than restated here,
    which keeps the rebuild correct no matter which earlier migrations added
    columns — a hand-written ``CREATE TABLE`` would silently drop whatever it
    had not been updated to know about.

    **Requires foreign-key enforcement to be off.** With it on, the ``DROP``
    fails against any table referencing this one. :func:`~lore_eden.store.
    migrations.run_migrations` disables it for the duration of a run, which is
    the context this is meant to be called from.

    A no-op when the table is absent or the column is already nullable, so it
    is safe to re-run.
    """
    if not table_exists(session, table) or column_is_nullable(session, table, column):
        return

    info = _table_info(session, table)
    names = [row[1] for row in info]
    definitions = [_column_clause(row, column) for row in info]
    definitions.extend(_foreign_key_clauses(session, table))
    indexes = _index_statements(session, table)

    temporary = f"{table}__relax_{column}"
    column_list = ", ".join(_quote(name) for name in names)
    session.execute(text(f"CREATE TABLE {_quote(temporary)} ({', '.join(definitions)})"))
    session.execute(
        text(
            f"INSERT INTO {_quote(temporary)} ({column_list}) "
            f"SELECT {column_list} FROM {_quote(table)}"
        )
    )
    session.execute(text(f"DROP TABLE {_quote(table)}"))
    session.execute(text(f"ALTER TABLE {_quote(temporary)} RENAME TO {_quote(table)}"))
    for statement in indexes:
        session.execute(text(statement))


def _table_info(session: Session, table: str) -> Sequence[Any]:
    """``PRAGMA table_info`` rows: (cid, name, type, notnull, default, pk)."""
    if not table_exists(session, table):
        return []
    return session.execute(text(f"PRAGMA table_info({_quote(table)})")).fetchall()


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _column_clause(row: Any, relaxed: str) -> str:
    """One column definition for the rebuilt table, NOT NULL dropped on ``relaxed``."""
    name, column_type, notnull, default, primary_key = row[1], row[2], row[3], row[4], row[5]
    parts = [_quote(name), column_type or "VARCHAR"]
    if primary_key:
        parts.append("PRIMARY KEY")
    if notnull and name != relaxed:
        parts.append("NOT NULL")
    if default is not None:
        parts.append(f"DEFAULT {default}")
    return " ".join(parts)


def _foreign_key_clauses(session: Session, table: str) -> list[str]:
    """Restate this table's outgoing foreign keys, which a rebuild would drop."""
    rows = session.execute(text(f"PRAGMA foreign_key_list({_quote(table)})")).fetchall()
    return [
        f"FOREIGN KEY({_quote(row[3])}) REFERENCES {_quote(row[2])} ({_quote(row[4])})"
        for row in rows
    ]


def _index_statements(session: Session, table: str) -> list[str]:
    """The ``CREATE INDEX`` statements to replay; a dropped table takes its indexes."""
    rows = session.execute(
        text(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=:table "
            "AND sql IS NOT NULL"
        ),
        {"table": table},
    ).fetchall()
    return [row[0] for row in rows]

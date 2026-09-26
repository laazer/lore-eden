"""Copy-on-write history for a mutable SQLite row.

Extracted from loregarden's `services/memory_history.py`, which keeps the
versions an `INSERT … ON CONFLICT DO UPDATE` would otherwise destroy. The host
names its history table, its key and the content columns; this writes the prior
version inside the caller's transaction, before the update replaces it.

Rules it keeps, all of which the source learned the hard way:

- **A create writes nothing**, and neither does an update that changes no
  content column — neither replaces any information.
- **Unknown attribution is NULL**, never `''`. A NOT NULL DEFAULT on a field
  nobody measured manufactures a measurement, and "someone said nothing" is not
  "nobody said".
- **Growth is bounded**: the newest `keep` versions per key survive, pruned in
  the same transaction. Version numbers keep counting past the prune, so a
  trimmed history reads as "versions 31–50", not as a row edited twenty times.

Plain `sqlite3`; no ORM. Identifiers are validated at construction because they
are interpolated into SQL — values never are.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED = (
    "version",
    "became_current_at",
    "superseded_at",
    "superseded_by",
    "change_note",
)


def _identifier(name: str) -> str:
    if not _IDENTIFIER.match(name):
        raise ValueError(f"not a safe SQL identifier: {name!r}")
    return name


@dataclass(frozen=True)
class RowHistory:
    """History for one table. `columns` are the content columns a version records."""

    table: str
    key_column: str
    columns: Sequence[str]
    keep: int = 20

    def __post_init__(self) -> None:
        for name in (self.table, self.key_column, *self.columns):
            _identifier(name)
        clash = set(self.columns) & {self.key_column, *_RESERVED}
        if clash:
            raise ValueError(f"content columns collide with history columns: {sorted(clash)}")
        if self.keep < 1:
            raise ValueError("keep must be at least 1")

    def ensure_schema(self, conn: sqlite3.Connection) -> None:
        content = ",\n".join(f"    {name}" for name in self.columns)
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {self.table} (
                {self.key_column} TEXT NOT NULL,
                version INTEGER NOT NULL,
            {content},
                became_current_at TEXT NULL,
                superseded_at TEXT NOT NULL,
                superseded_by TEXT NULL,
                change_note TEXT NULL,
                PRIMARY KEY ({self.key_column}, version)
            );
            """
        )

    def record_superseded(
        self,
        conn: sqlite3.Connection,
        *,
        key: str,
        prior: Mapping[str, Any],
        incoming: Mapping[str, Any],
        superseded_at: str,
        became_current_at: str | None = None,
        writer: str | None = None,
        note: str | None = None,
    ) -> int | None:
        """Keep `prior` before `incoming` replaces it. Returns the version written, or None."""
        before = tuple(prior[name] for name in self.columns)
        if before == tuple(incoming[name] for name in self.columns):
            return None
        (latest,) = conn.execute(
            f"SELECT COALESCE(MAX(version), 0) FROM {self.table} WHERE {self.key_column} = ?",
            (key,),
        ).fetchone()
        version = int(latest) + 1
        names = ", ".join(self.columns)
        marks = ", ".join("?" for _ in self.columns)
        conn.execute(
            f"INSERT INTO {self.table} ({self.key_column}, version, {names}, "
            "became_current_at, superseded_at, superseded_by, change_note) "
            f"VALUES (?, ?, {marks}, ?, ?, ?, ?)",
            (key, version, *before, became_current_at, superseded_at, writer, note),
        )
        conn.execute(
            f"DELETE FROM {self.table} WHERE {self.key_column} = ? AND version <= ?",
            (key, version - self.keep),
        )
        return version

    def versions(self, conn: sqlite3.Connection, key: str) -> list[dict[str, Any]]:
        """Every retained version for `key`, oldest first."""
        cursor = conn.execute(
            f"SELECT * FROM {self.table} WHERE {self.key_column} = ? ORDER BY version",
            (key,),
        )
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

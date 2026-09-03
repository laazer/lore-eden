"""UTC-aware datetimes at the storage and serialization boundaries.

A store writes timestamps as UTC-aware (``datetime.now(timezone.utc)``), but a
plain ``DateTime`` column on SQLite drops the zone on write and hands back a
**naive** value on read. ``naive.isoformat()`` then emits
``2026-08-08T14:19:57.465660`` — no ``Z``, no offset — and ECMAScript parses an
offset-less date-time as *local* time. A UI shows a UTC instant as the viewer's
wall clock, wrong by their entire UTC offset.

That is a bug worth naming precisely, because it is silent: every timestamp
looks plausible, and the error is only visible to a reader in a non-UTC zone
asking "which run failed, and when?".

So the zone is re-attached here, at the boundary where the value is known to be
UTC, rather than guessed at by each reader.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """The current instant, UTC-aware.

    Named rather than inlined so a store and its tests agree on one clock, and
    so ``datetime.utcnow()`` — which returns a *naive* value and is deprecated
    from 3.12 — has an obvious replacement.
    """
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Return ``value`` UTC-aware, tagging a naive value as the UTC it already is.

    The naive branch is a claim about the caller's storage, not a guess: it is
    correct only where every write went through :func:`utcnow`. A store that
    writes local time needs its own conversion, not this one.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso_utc(value: datetime | None) -> str | None:
    """ISO-8601 with an explicit offset, or ``None`` — safe for a JS ``Date``."""
    if value is None:
        return None
    return as_utc(value).isoformat()

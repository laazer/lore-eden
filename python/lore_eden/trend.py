"""Compare two readings of the same metrics and say what moved.

Extracted from loregarden's memory graph health. The discipline it encodes: a
single reading tells you almost nothing, so a reading is always compared with
the previous one; small wobbles are not movement; and the report names **one**
metric to watch — the highest-priority one that got worse — because a report
with five action items gets none of them done.

Metrics are "adverse": a rise is bad news. The caller orders them by priority.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Movement:
    metric: str
    was: float
    now: float

    @property
    def worse(self) -> bool:
        return self.now > self.was


@dataclass(frozen=True)
class TrendReport:
    moved: list[Movement]
    #: The single metric to act on, or None when nothing got worse.
    watch: str | None


def compare(
    previous: Mapping[str, float],
    current: Mapping[str, float],
    *,
    adverse: Sequence[str],
    noteworthy: float,
) -> TrendReport:
    """Movements of at least `noteworthy` in the `adverse` metrics, in priority order."""
    moved = [
        Movement(metric, previous[metric], current[metric])
        for metric in adverse
        if abs(current[metric] - previous[metric]) >= noteworthy
    ]
    worse = [movement for movement in moved if movement.worse]
    return TrendReport(moved=moved, watch=worse[0].metric if worse else None)

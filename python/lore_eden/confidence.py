"""Confidence in a claim, from graded outcomes: a Beta posterior.

Extracted from loregarden's `services/learning_confidence.py`, where it scores
a remembered lesson by how the runs it was briefed into actually ended. Nothing
here knows what a lesson or a run is. A host grades each outcome into a unit of
evidence — how much of it counts as success, how much as failure — and this
folds the evidence into a posterior.

Three properties, each of which the rejected alternative lacked:

- **Order-independent.** The posterior is the prior plus a sum, and the sum is
  `math.fsum`, which is correctly rounded — so the result is identical, to the
  bit, however the evidence is ordered. A running multiplier ("nudge the score
  by 10% per outcome") is not: apply the same events in another order and the
  answer moves.
- **Sample-size aware.** `lower_bound` narrows as evidence accumulates, so one
  lucky success and fifty successes are told apart even when their means agree.
  `trusted` is a corroboration gate on that bound: one observation cannot mint
  a trusted claim on the uniform prior.
- **Asymmetric recency.** Evidence decays with age against an explicit
  `as_of`, and failure decays more slowly than success by default, so a fresh
  failure outweighs an old success.

The weights are the host's. A defect in them is a host decision; a defect in
the arithmetic is this module's.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

#: One-sided 95% normal quantile, for `lower_bound`.
_Z = 1.645


@dataclass(frozen=True)
class Evidence:
    """One graded outcome. `success + failure` is normally 1; neither may be negative."""

    success: float
    failure: float
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.success < 0 or self.failure < 0:
            raise ValueError("evidence weights must be non-negative")


@dataclass(frozen=True)
class Recency:
    """Half-lives, in days, for success and failure evidence."""

    success_half_life_days: float = 45.0
    failure_half_life_days: float = 180.0


@dataclass(frozen=True)
class Confidence:
    """A Beta(alpha, beta) posterior and the raw count of observations behind it.

    `observations` is kept apart from the weighted evidence so "never observed"
    (0) cannot be mistaken for "observed long ago" (decayed to near the prior).
    """

    alpha: float
    beta: float
    observations: int

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def lower_bound(self) -> float:
        total = self.alpha + self.beta
        variance = (self.alpha * self.beta) / (total * total * (total + 1.0))
        return max(0.0, self.mean - _Z * math.sqrt(variance))

    def trusted(self, threshold: float = 0.5) -> bool:
        return self.lower_bound >= threshold


DEFAULT_RECENCY = Recency()


def _decay(age_days: float, half_life_days: float) -> float:
    return 0.5 ** (max(age_days, 0.0) / half_life_days)


def score(
    evidence: Iterable[Evidence],
    *,
    as_of: datetime,
    prior: tuple[float, float] = (1.0, 1.0),
    recency: Recency = DEFAULT_RECENCY,
) -> Confidence:
    """Fold evidence into a posterior. Identical for any ordering of `evidence`."""
    successes = [prior[0]]
    failures = [prior[1]]
    count = 0
    for item in evidence:
        age_days = (as_of - item.observed_at).total_seconds() / 86400.0
        successes.append(item.success * _decay(age_days, recency.success_half_life_days))
        failures.append(item.failure * _decay(age_days, recency.failure_half_life_days))
        count += 1
    return Confidence(alpha=math.fsum(successes), beta=math.fsum(failures), observations=count)

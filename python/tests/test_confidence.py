"""A Beta posterior: order-independent, sample-size aware, recency-asymmetric."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest
from lore_eden.confidence import Evidence, score

AS_OF = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _win(days_ago: float = 0) -> Evidence:
    return Evidence(1.0, 0.0, AS_OF - timedelta(days=days_ago))


def _loss(days_ago: float = 0) -> Evidence:
    return Evidence(0.0, 1.0, AS_OF - timedelta(days=days_ago))


def test_any_ordering_scores_identically_to_the_bit():
    rng = random.Random(3)
    evidence = [
        Evidence(w, 1 - w, AS_OF - timedelta(days=rng.uniform(0, 500)))
        for w in (rng.choice((0.0, 0.25, 0.75, 1.0)) for _ in range(300))
    ]
    expected = score(evidence, as_of=AS_OF)
    for seed in range(10):
        shuffled = evidence[:]
        random.Random(seed).shuffle(shuffled)
        assert score(shuffled, as_of=AS_OF) == expected


def test_more_evidence_narrows_the_bound_and_one_success_is_not_trusted():
    one = score([_win()], as_of=AS_OF)
    many = score([_win()] * 10, as_of=AS_OF)
    assert many.lower_bound > one.lower_bound
    assert not one.trusted()
    assert many.trusted()


def test_a_fresh_failure_outweighs_an_old_success():
    assert score([_win(days_ago=180), _loss()], as_of=AS_OF).mean < 0.5


def test_no_evidence_is_the_prior_with_zero_observations():
    empty = score([], as_of=AS_OF)
    assert (empty.alpha, empty.beta, empty.observations) == (1.0, 1.0, 0)


def test_negative_weights_are_refused():
    with pytest.raises(ValueError):
        Evidence(-0.1, 1.0, AS_OF)

"""Near-duplicate pairs and two-reading comparison."""

from __future__ import annotations

from lore_eden.similarity import jaccard, near_duplicate_pairs
from lore_eden.trend import compare


def test_jaccard_is_zero_for_an_empty_side():
    assert jaccard(set(), {"a"}) == 0.0
    assert jaccard({"a", "b"}, {"b", "c"}) == 1 / 3


def test_pairs_are_ordered_capped_and_skip_known_disagreements():
    terms = {
        "a": {"retry", "idempotent", "post"},
        "b": {"retry", "idempotent", "post"},
        "c": {"retry", "idempotent", "put"},
        "d": {"cache"},
    }
    pairs = near_duplicate_pairs(terms, threshold=0.5, exclude={frozenset(("a", "b"))}, limit=5)
    assert [(p.left, p.right) for p in pairs] == [("a", "c"), ("b", "c")]
    assert near_duplicate_pairs(terms, threshold=0.5, limit=1)[0].similarity == 1.0


def test_compare_ignores_wobble_and_names_the_first_adverse_riser():
    report = compare(
        {"orphans": 10.0, "stale": 5.0, "cold": 1.0},
        {"orphans": 11.0, "stale": 9.0, "cold": 0.0},
        adverse=["orphans", "stale", "cold"],
        noteworthy=2.0,
    )
    assert [m.metric for m in report.moved] == ["stale"]
    assert report.watch == "stale"


def test_compare_watches_nothing_when_everything_improved():
    report = compare({"x": 9.0}, {"x": 1.0}, adverse=["x"], noteworthy=2.0)
    assert report.watch is None and report.moved[0].worse is False

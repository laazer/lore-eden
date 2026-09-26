"""Near-duplicate pairs by term-set overlap.

Extracted from loregarden's memory curation, which proposes merges between
learnings that read as the same idea. Deterministic and index-free: a Jaccard
score over caller-supplied term sets, every pair compared once, ties broken on
ids so the same input always yields the same list.

Two rules the source learned: pairs the caller knows **disagree** are never
proposed (two things that contradict each other are not duplicates, however
alike they read), and the result is **capped** — a proposal list nobody can
finish is a list nobody acts on.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class SimilarPair:
    left: str
    right: str
    similarity: float


def jaccard(a: Collection[str], b: Collection[str]) -> float:
    """|a ∩ b| / |a ∪ b|, and 0.0 when either side is empty."""
    left, right = set(a), set(b)
    return len(left & right) / len(left | right) if left and right else 0.0


def near_duplicate_pairs(
    terms: Mapping[str, Collection[str]],
    *,
    threshold: float,
    exclude: Collection[frozenset[str]] = (),
    limit: int = 20,
) -> list[SimilarPair]:
    """Pairs of ids whose term sets overlap at or above `threshold`, most alike first.

    O(n²) in the number of ids; callers bound `terms` themselves.
    """
    ids = sorted(terms)
    found: list[SimilarPair] = []
    for index, left in enumerate(ids):
        for right in ids[index + 1 :]:
            if frozenset((left, right)) in exclude:
                continue
            score = jaccard(terms[left], terms[right])
            if score >= threshold:
                found.append(SimilarPair(left, right, score))
    found.sort(key=lambda pair: (-pair.similarity, pair.left, pair.right))
    return found[:limit]

"""What something cost, and how much of that figure is real.

Extracted from loregarden's `run_token_usage`, whose thesis is the whole reason
this is worth sharing: **zero and "nobody measured it" are different numbers.**

A run that finished before the usage columns existed reports nothing. A local
adapter reports nothing. A run killed mid-flight never prints its usage event.
Summing those as zero is how a cost report quietly understates itself, and it is
not recoverable afterwards — the average has already been taken, and nothing in
the stored data says which entries were guesses.

So every amount here is ``int | None``, a group where nobody reported a measure
totals ``None`` rather than ``0``, and every total carries ``measured_entries``
beside ``unmeasured_entries`` so a reader can see how much of the answer is
real. An entry that genuinely spent nothing still totals ``0`` and still counts
as measured — which is exactly the case a nullable figure keeps separable.

## Generalised from tokens to any measure

loregarden counts four token columns. abacus counts money, and gaia will count
requests. So the measures are a host-declared vocabulary rather than four
fields: ``{"input_tokens": 1200, "cents": None}`` is a perfectly good entry, and
the accounting rules are the same whatever is being counted.

## What did not come across, and why

loregarden's `usage_service` is 1386 lines of reading Claude's credentials file
and keychain, refreshing OAuth tokens, and parsing two vendors' usage APIs. That
is an integration with particular CLIs, not cost accounting.

Its `usage_limits` module is not what its name suggests either: it recognises a
provider's *quota refusal* in CLI output — regexes over Codex and Claude prose,
to tell an operator that waiting is the fix. Useful, and nothing to do with
enforcing a spend limit. There is therefore **no generic limit enforcement to
extract**; a host that wants budget alerts is writing them, not adopting them.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, NewType, Sequence

from lore_eden.cache import Cache, NullCache
from lore_eden.timestamps import utcnow

#: What is being counted — ``input_tokens``, ``cents``, ``requests``. The host's
#: vocabulary, because the whole point of generalising this was that one host
#: counts tokens and another counts money.
Measure = NewType("Measure", str)


@dataclass(frozen=True)
class UsageRecord:
    """One thing that was spent, attributed to something.

    ``subject_id`` is what did the spending — a run, a request, a job.
    ``group_key`` is what a host wants to add up by: a stage, a service, a
    project. Both are opaque strings, because a library that knew which was
    which would be that host's library.

    An amount of ``None`` means *not measured*. Leaving a measure out of the
    mapping entirely means the same thing, and the two are treated identically:
    a caller should not have to know whether an absent figure is absent or
    explicitly unknown.
    """

    subject_id: str
    group_key: str
    amounts: Mapping[Measure, int | None] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class UsageTotals:
    """A group's totals, and how complete they are.

    Read ``amounts`` beside ``unmeasured_entries``. A total over a group that is
    mostly unmeasured is a floor, not a cost, and presenting it as a cost is the
    failure this type exists to make visible.
    """

    key: str
    entries: int = 0
    measured_entries: int = 0
    unmeasured_entries: int = 0
    amounts: Mapping[Measure, int | None] = field(default_factory=dict)

    def total_of(self, measures: Sequence[Measure]) -> int | None:
        """The sum of the named measures, or None if none of them was measured.

        Named explicitly rather than summing everything: tokens and cents in one
        record are two currencies, and adding them would produce a number with
        no unit that still renders fine in a dashboard.
        """
        known = [
            self.amounts[measure]
            for measure in measures
            if self.amounts.get(measure) is not None
        ]
        return sum(known) if known else None  # type: ignore[arg-type]

    @property
    def complete(self) -> bool:
        """Whether every entry in this group reported something."""
        return self.entries > 0 and self.unmeasured_entries == 0


def add_amount(total: int | None, value: int | None) -> int | None:
    """Fold one figure into a running total, leaving an unmeasured total alone.

    An unreported figure contributes nothing *and does not make the total zero*:
    the total only becomes a number once something has reported one.
    """
    if value is None:
        return total
    return value if total is None else total + value


def is_measured(record: UsageRecord) -> bool:
    """Whether this entry reported any figure at all."""
    return any(value is not None for value in record.amounts.values())


def fold(records: Sequence[UsageRecord], key: str = "") -> UsageTotals:
    """Sum one group of entries into a single :class:`UsageTotals`."""
    amounts: dict[Measure, int | None] = {}
    measured = 0
    for record in records:
        if is_measured(record):
            measured += 1
        for measure, value in record.amounts.items():
            amounts[measure] = add_amount(amounts.get(measure), value)
    return UsageTotals(
        key=key,
        entries=len(records),
        measured_entries=measured,
        unmeasured_entries=len(records) - measured,
        amounts=amounts,
    )


def group_by_key(records: Sequence[UsageRecord]) -> list[UsageTotals]:
    """One :class:`UsageTotals` per ``group_key``, ordered by name.

    A group that was worked twice is one group, not two: a retry is part of what
    the thing cost, and separating the attempts is what made "how much did the
    rework cost" unanswerable in the source this came from.
    """
    grouped: dict[str, list[UsageRecord]] = defaultdict(list)
    for record in records:
        grouped[record.group_key].append(record)
    return [fold(grouped[key], key) for key in sorted(grouped)]


def usage_tag(group_key: str) -> str:
    """What a cached total derives from."""
    return f"usage:{group_key}"


class Usage:
    """Recording what was spent, and adding it up.

    The store is a protocol and the cache is a protocol, so a host with Postgres
    and its own ORM implements two methods and installs no extra. No session,
    connection or ORM model appears in any signature here.
    """

    def __init__(self, store: Any, cache: Cache | None = None) -> None:
        self._store = store
        self._cache = cache if cache is not None else NullCache()

    def record(self, record: UsageRecord) -> UsageRecord:
        """Store one entry and drop any total it would change."""
        stored = self._store.add_usage(record)
        self._cache.invalidate_tags(frozenset({usage_tag(stored.group_key)}))
        return stored

    def totals(
        self,
        group_keys: Sequence[str],
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Mapping[str, UsageTotals]:
        """Totals per group over an optional window, in one query.

        Every key asked about is present, so a group with no entries in the
        window reads as zero entries rather than being missing — the answer to
        "what did this cost" is "nothing recorded", not silence.
        """
        cached: dict[str, UsageTotals] = {}
        wanted: list[str] = []
        for group_key in group_keys:
            found = self._cache.get(self._key(group_key, since, until))
            if found.found:
                cached[group_key] = found.value
            else:
                wanted.append(group_key)

        if wanted:
            records = self._store.usage_for(wanted, since=since, until=until)
            for group_key in wanted:
                totals = fold(list(records.get(group_key, ())), group_key)
                self._cache.set(
                    self._key(group_key, since, until),
                    totals,
                    frozenset({usage_tag(group_key)}),
                )
                cached[group_key] = totals
        return {group_key: cached[group_key] for group_key in group_keys}

    def entries(
        self,
        group_keys: Sequence[str],
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Mapping[str, Sequence[UsageRecord]]:
        """The entries themselves, for a caller that wants to group differently."""
        return self._store.usage_for(group_keys, since=since, until=until)

    @staticmethod
    def _key(group_key: str, since: datetime | None, until: datetime | None) -> str:
        """The window is part of the key.

        Two ranges over one group are two answers, and a cache keyed on the
        group alone would serve last month's total for this month's question.
        """
        return f"usage-totals:{group_key}:{since.isoformat() if since else ''}:{until.isoformat() if until else ''}"

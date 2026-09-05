"""Usage accounting, on every backend.

The property that makes this worth sharing is not that it adds numbers up. It is
that it refuses to add up numbers nobody reported. A cost report that sums
unmeasured entries as zero understates itself, and the understatement is not
recoverable afterwards — by the time anyone asks, the average has been taken and
nothing in the stored data says which entries were guesses.

So most of what follows is about `None`, not about arithmetic.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any, Iterator

import pytest
from lore_eden.cache import DictCache, NullCache
from lore_eden.store.memory import InMemoryUsageStore
from lore_eden.timestamps import utcnow
from lore_eden.usage import Measure, Usage, UsageRecord, fold, group_by_key

POSTGRES_DSN_ENV = "LORE_EDEN_TEST_POSTGRES_DSN"
REQUIRE_POSTGRES_ENV = "LORE_EDEN_REQUIRE_POSTGRES"

TOKENS = Measure("input_tokens")
CENTS = Measure("cents")


def _sql_store(url: str) -> Iterator[Any]:
    from sqlmodel import Session, SQLModel, create_engine

    from lore_eden.store.sql import SqlUsageStore

    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield SqlUsageStore(session)
    SQLModel.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(params=["memory", "sqlite", "postgres"])
def store(request, tmp_path) -> Iterator[Any]:
    if request.param == "memory":
        yield InMemoryUsageStore()
        return
    if request.param == "sqlite":
        yield from _sql_store(f"sqlite:///{tmp_path / 'usage.db'}")
        return
    dsn = os.environ.get(POSTGRES_DSN_ENV, "")
    if not dsn:
        if os.environ.get(REQUIRE_POSTGRES_ENV) == "1":
            pytest.fail(
                f"{REQUIRE_POSTGRES_ENV}=1 but {POSTGRES_DSN_ENV} is unset — the "
                "Postgres pass would have skipped silently."
            )
        pytest.skip(f"set {POSTGRES_DSN_ENV} to run the Postgres pass")
    yield from _sql_store(dsn)


@pytest.fixture
def usage(store) -> Usage:
    return Usage(store, DictCache())


class TestUnmeasuredIsNotZero:
    def test_a_group_nobody_measured_totals_none(self, usage) -> None:
        usage.record(UsageRecord("r1", "svc", {TOKENS: None}))
        usage.record(UsageRecord("r2", "svc", {}))

        totals = usage.totals(["svc"])["svc"]

        assert totals.amounts.get(TOKENS) is None
        assert (totals.entries, totals.measured_entries) == (2, 0)

    def test_an_entry_that_genuinely_spent_nothing_totals_zero_and_counts(
        self, usage
    ) -> None:
        """The case a nullable figure keeps separable, and the reason this is not
        just 'treat missing as zero'."""
        usage.record(UsageRecord("r1", "svc", {TOKENS: 0}))

        totals = usage.totals(["svc"])["svc"]

        assert totals.amounts[TOKENS] == 0
        assert (totals.measured_entries, totals.unmeasured_entries) == (1, 0)

    def test_one_measured_entry_makes_the_total_a_number(self, usage) -> None:
        usage.record(UsageRecord("r1", "svc", {TOKENS: None}))
        usage.record(UsageRecord("r2", "svc", {TOKENS: 40}))

        totals = usage.totals(["svc"])["svc"]

        assert totals.amounts[TOKENS] == 40
        assert totals.unmeasured_entries == 1
        assert totals.complete is False

    def test_completeness_is_reportable(self, usage) -> None:
        usage.record(UsageRecord("r1", "svc", {CENTS: 10}))

        assert usage.totals(["svc"])["svc"].complete is True

    def test_an_absent_measure_and_an_explicit_none_are_the_same(self) -> None:
        """A caller should not have to know whether a figure is missing or
        explicitly unknown; both mean nobody measured it."""
        implicit = fold([UsageRecord("r", "g", {})], "g")
        explicit = fold([UsageRecord("r", "g", {TOKENS: None})], "g")

        assert implicit.amounts.get(TOKENS) is None
        assert explicit.amounts[TOKENS] is None
        assert implicit.measured_entries == explicit.measured_entries == 0


class TestMeasuresAreTheHostsVocabulary:
    def test_tokens_and_money_live_in_one_ledger(self, usage) -> None:
        """The generalisation that made this shared: one host counts tokens,
        another counts money, and the accounting rules are identical."""
        usage.record(UsageRecord("r1", "svc", {TOKENS: 1200, CENTS: 30}))
        usage.record(UsageRecord("r2", "svc", {TOKENS: 800}))

        totals = usage.totals(["svc"])["svc"]

        assert totals.amounts[TOKENS] == 2000
        assert totals.amounts[CENTS] == 30

    def test_summing_across_measures_is_opt_in_and_named(self, usage) -> None:
        """Adding tokens to cents would produce a number with no unit that still
        renders fine in a dashboard, so the caller names what to add."""
        usage.record(UsageRecord("r1", "svc", {TOKENS: 100, CENTS: 5}))

        totals = usage.totals(["svc"])["svc"]

        assert totals.total_of([TOKENS]) == 100
        assert totals.total_of([CENTS]) == 5

    def test_summing_only_unmeasured_measures_is_none(self, usage) -> None:
        usage.record(UsageRecord("r1", "svc", {TOKENS: None}))

        assert usage.totals(["svc"])["svc"].total_of([TOKENS]) is None


class TestWindows:
    def test_the_range_is_half_open_so_windows_tile(self, usage) -> None:
        """Inclusive of `since`, exclusive of `until` — the arithmetic that stops
        one entry appearing in both March and April."""
        boundary = utcnow()
        usage.record(UsageRecord("before", "svc", {CENTS: 1}, boundary - timedelta(seconds=1)))
        usage.record(UsageRecord("on", "svc", {CENTS: 10}, boundary))
        usage.record(UsageRecord("after", "svc", {CENTS: 100}, boundary + timedelta(seconds=1)))

        earlier = usage.totals(["svc"], until=boundary)["svc"]
        later = usage.totals(["svc"], since=boundary)["svc"]

        assert earlier.amounts[CENTS] == 1
        assert later.amounts[CENTS] == 110
        assert earlier.entries + later.entries == 3

    def test_a_group_with_nothing_in_the_window_is_still_answered(self, usage) -> None:
        """"Nothing recorded" is an answer; a missing key is silence."""
        usage.record(UsageRecord("r1", "svc", {CENTS: 5}))

        totals = usage.totals(["svc", "never-used"])["never-used"]

        assert totals.entries == 0
        assert totals.amounts == {}


class TestAttributionSurvivesARestart:
    def test_entries_are_readable_by_subject_and_group(self, store) -> None:
        """Per-run attribution is the whole point: a total nobody can decompose
        cannot be argued with."""
        first = Usage(store)
        first.record(UsageRecord("run-1", "stage-a", {TOKENS: 10}))
        first.record(UsageRecord("run-2", "stage-a", {TOKENS: 20}))
        first.record(UsageRecord("run-3", "stage-b", {TOKENS: 5}))

        # A second service over the same store — what a restart looks like.
        entries = Usage(store).entries(["stage-a", "stage-b"])

        assert {record.subject_id for record in entries["stage-a"]} == {"run-1", "run-2"}
        assert [record.subject_id for record in entries["stage-b"]] == ["run-3"]

    def test_grouping_is_available_without_the_store(self) -> None:
        records = [
            UsageRecord("r1", "b", {TOKENS: 1}),
            UsageRecord("r2", "a", {TOKENS: 2}),
            UsageRecord("r3", "a", {TOKENS: 3}),
        ]

        grouped = group_by_key(records)

        assert [totals.key for totals in grouped] == ["a", "b"]
        assert grouped[0].amounts[TOKENS] == 5


class TestCaching:
    def test_a_total_is_served_from_cache_until_something_is_recorded(
        self, store
    ) -> None:
        cache = DictCache()
        usage = Usage(store, cache)
        usage.record(UsageRecord("r1", "svc", {CENTS: 5}))
        assert usage.totals(["svc"])["svc"].amounts[CENTS] == 5

        usage.record(UsageRecord("r2", "svc", {CENTS: 7}))

        assert usage.totals(["svc"])["svc"].amounts[CENTS] == 12

    def test_two_windows_over_one_group_do_not_answer_each_other(self, store) -> None:
        """A cache keyed on the group alone would serve last month's total for
        this month's question."""
        cache = DictCache()
        usage = Usage(store, cache)
        boundary = utcnow()
        usage.record(UsageRecord("old", "svc", {CENTS: 1}, boundary - timedelta(hours=1)))
        usage.record(UsageRecord("new", "svc", {CENTS: 50}, boundary))

        everything = usage.totals(["svc"])["svc"]
        recent = usage.totals(["svc"], since=boundary)["svc"]

        assert everything.amounts[CENTS] == 51
        assert recent.amounts[CENTS] == 50

    def test_recording_against_one_group_does_not_clear_another(self, store) -> None:
        cache = DictCache()
        usage = Usage(store, cache)
        usage.record(UsageRecord("r1", "kept", {CENTS: 3}))
        usage.totals(["kept"])
        entries_before = len(cache._values)

        usage.record(UsageRecord("r2", "other", {CENTS: 9}))

        assert entries_before == 1
        assert cache.get(usage._key("kept", None, None)).found is True

    def test_without_a_cache_the_answers_are_the_same(self, store) -> None:
        usage = Usage(store, NullCache())
        usage.record(UsageRecord("r1", "svc", {CENTS: 5}))
        usage.record(UsageRecord("r2", "svc", {CENTS: None}))

        totals = usage.totals(["svc"])["svc"]

        assert totals.amounts[CENTS] == 5
        assert (totals.entries, totals.unmeasured_entries) == (2, 1)

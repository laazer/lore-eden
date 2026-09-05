"""The ledger, on every backend, and the properties that make it worth having.

Six loremaker backlogs each plan to build this. What makes one worth sharing is
not that it stores events — anything stores events — but four properties that
are easy to claim and easy to get wrong:

- what it holds is **opaque**, so it can serve a cost line and an onboarding
  step without knowing what either is;
- it **refuses to forget**: no update, no delete, and a taken sequence number is
  an error rather than an overwrite;
- replay is **deterministic**, so state rebuilt twice is the same state;
- and the hash chain makes it **evidence**, not just a record — an edited
  payload is detectable after the fact.

Each is asserted here, against memory, SQLite and Postgres alike.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import pytest
from lore_eden.ledger import (
    EntityType,
    EventType,
    IdempotencyKeyConflict,
    Ledger,
    LedgerSequenceConflict,
    compute_checksum,
    replace_payload_for_testing,
)
from lore_eden.store.memory import InMemoryLedgerStore

POSTGRES_DSN_ENV = "LORE_EDEN_TEST_POSTGRES_DSN"
REQUIRE_POSTGRES_ENV = "LORE_EDEN_REQUIRE_POSTGRES"

COST = EntityType("cost_line")
ONBOARDING = EntityType("onboarding")
RECORDED = EventType("recorded")
AMENDED = EventType("amended")


def _sql_store(url: str) -> Iterator[Any]:
    from lore_eden.store.sql import SqlLedgerStore
    from sqlmodel import Session, SQLModel, create_engine

    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield SqlLedgerStore(session)
    SQLModel.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(params=["memory", "sqlite", "postgres"])
def store(request, tmp_path) -> Iterator[Any]:
    if request.param == "memory":
        yield InMemoryLedgerStore()
        return
    if request.param == "sqlite":
        yield from _sql_store(f"sqlite:///{tmp_path / 'ledger.db'}")
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
def ledger(store) -> Ledger:
    return Ledger(store)


def record(ledger: Ledger, entity_id: str, entity_type: EntityType, **payload):
    return ledger.append(
        event_type=RECORDED, entity_id=entity_id, entity_type=entity_type, payload=payload
    )


class TestItHoldsWhateverTheHostPutsIn:
    def test_two_unrelated_domains_share_one_ledger(self, ledger):
        """The claim that makes this one component rather than six: nothing
        here knows what a cost line or an onboarding step is."""
        record(ledger, "cl-1", COST, amount_cents=1250, currency="USD")
        record(ledger, "user-9", ONBOARDING, step="email_verified", attempts=2)

        cost = ledger.events_for("cl-1", COST)
        onboarding = ledger.events_for("user-9", ONBOARDING)

        assert cost[0].payload == {"amount_cents": 1250, "currency": "USD"}
        assert onboarding[0].payload == {"step": "email_verified", "attempts": 2}

    def test_streams_with_the_same_id_but_different_types_do_not_mix(self, ledger):
        record(ledger, "shared", COST, which="cost")
        record(ledger, "shared", ONBOARDING, which="onboarding")

        assert len(ledger.events_for("shared", COST)) == 1
        assert ledger.events_for("shared", ONBOARDING)[0].payload == {"which": "onboarding"}

    def test_a_nested_payload_survives_the_round_trip(self, ledger):
        record(ledger, "cl-2", COST, breakdown={"compute": 10, "storage": [1, 2]})

        stored = ledger.events_for("cl-2", COST)[0]

        assert stored.payload["breakdown"] == {"compute": 10, "storage": [1, 2]}


class TestItRefusesToForget:
    def test_the_store_offers_no_way_to_change_or_remove_an_event(self, store):
        """The immutability guarantee is the *surface*, not a rule each backend
        is asked to remember. Nothing can call what does not exist."""
        offered = {name for name in dir(store) if not name.startswith("_")}

        assert not {name for name in offered if "update" in name or "delete" in name}

    def test_a_taken_sequence_number_is_refused_not_overwritten(self, ledger, store):
        first = record(ledger, "cl-3", COST, amount_cents=1).event

        with pytest.raises(LedgerSequenceConflict):
            store.append_event(first)

        assert len(ledger.events_for("cl-3", COST)) == 1

    def test_the_refusal_leaves_the_earlier_event_intact(self, ledger, store):
        """Refused, not partially applied: the store must still be usable and
        the original value still readable."""
        record(ledger, "cl-4", COST, amount_cents=99)
        clash = ledger.events_for("cl-4", COST)[0]

        with pytest.raises(LedgerSequenceConflict):
            store.append_event(clash)

        assert ledger.events_for("cl-4", COST)[0].payload == {"amount_cents": 99}
        assert record(ledger, "cl-4", COST, amount_cents=100).event.sequence_number == 2

    def test_sequence_numbers_start_at_one_and_increment(self, ledger):
        numbers = [record(ledger, "cl-5", COST, n=index).event.sequence_number for index in range(3)]

        assert numbers == [1, 2, 3]


class TestReplayIsDeterministic:
    @staticmethod
    def total(state: dict, event) -> dict:
        return {"total": state["total"] + event.payload.get("amount_cents", 0)}

    def test_the_same_ledger_replayed_twice_is_the_same_state(self, ledger):
        for amount in (100, 250, 30):
            record(ledger, "cl-6", COST, amount_cents=amount)

        first = ledger.replay("cl-6", COST, self.total, {"total": 0})
        second = ledger.replay("cl-6", COST, self.total, {"total": 0})

        assert first.state == second.state == {"total": 380}
        assert first.events_applied == second.events_applied == 3
        assert first.last_sequence_number == 3

    def test_replay_folds_in_sequence_order_not_insertion_order(self, ledger):
        """Order is the contract. A reducer that cares about order — most of
        them — is wrong for every event after the first if the store returns
        rows however they happened to be written."""
        for step in ("created", "renamed", "archived"):
            ledger.append(
                event_type=EventType(step), entity_id="cl-7", entity_type=COST, payload={}
            )

        def last_wins(state: dict, event) -> dict:
            return {"status": str(event.event_type)}

        assert ledger.replay("cl-7", COST, last_wins, {"status": ""}).state == {
            "status": "archived"
        }

    def test_an_empty_stream_replays_to_the_initial_state(self, ledger):
        result = ledger.replay("never-happened", COST, self.total, {"total": 0})

        assert result.state == {"total": 0}
        assert (result.events_applied, result.last_sequence_number) == (0, 0)


class TestTheChainMakesItEvidence:
    def test_an_untouched_stream_verifies(self, ledger):
        for amount in (1, 2, 3):
            record(ledger, "cl-8", COST, amount_cents=amount)

        report = ledger.verify_chain("cl-8", COST)

        assert report.intact is True
        assert (report.events_checked, report.broken_at_sequence) == (3, 0)

    def test_an_edited_payload_is_caught_and_located(self, ledger, store):
        """The property that survives a dishonest actor rather than a careless
        one. Simulated at the store, because a caller cannot do this through the
        ledger at all — which is the point."""
        for amount in (10, 20, 30):
            record(ledger, "cl-9", COST, amount_cents=amount)
        events = list(ledger.events_for("cl-9", COST))
        tampered = replace_payload_for_testing(events[1], {"amount_cents": 999})
        self._overwrite(store, events, tampered)

        report = ledger.verify_chain("cl-9", COST)

        assert report.intact is False
        assert report.broken_at_sequence == 2

    def test_recomputing_the_edited_rows_checksum_does_not_save_it(self, ledger, store):
        """The reason the checksum chains rather than standing alone: an actor
        who edits a payload *and* fixes that row's checksum still leaves the
        next event derived from the value they replaced."""
        for amount in (10, 20, 30):
            record(ledger, "cl-10", COST, amount_cents=amount)
        events = list(ledger.events_for("cl-10", COST))
        forged_payload = {"amount_cents": 999}
        forged = replace_payload_for_testing(events[1], forged_payload)
        forged = type(forged)(
            event_type=forged.event_type,
            entity_id=forged.entity_id,
            entity_type=forged.entity_type,
            payload=forged_payload,
            sequence_number=forged.sequence_number,
            checksum=compute_checksum(
                forged.event_type,
                forged.entity_id,
                forged.entity_type,
                forged.sequence_number,
                forged_payload,
                events[0].checksum,
            ),
            actor=forged.actor,
            idempotency_key=forged.idempotency_key,
            occurred_at=forged.occurred_at,
        )
        self._overwrite(store, events, forged)

        report = ledger.verify_chain("cl-10", COST)

        assert report.intact is False
        # The forged row itself verifies; the one after it does not.
        assert report.broken_at_sequence == 3

    @staticmethod
    def _overwrite(store, events, replacement) -> None:
        """Put a doctored event where a real one was, going around the ledger.

        There is no supported way to do this, so the test reaches past the
        protocol into each backend. That is the correct amount of awkward: if it
        were convenient, the guarantee would not be worth much.
        """
        rewritten = [
            replacement if event.sequence_number == replacement.sequence_number else event
            for event in events
        ]
        if hasattr(store, "_events"):
            store._events = rewritten
            return

        from lore_eden.store.sql import LedgerEventRow
        from sqlmodel import select

        row = store.session.exec(
            select(LedgerEventRow).where(
                LedgerEventRow.entity_id == replacement.entity_id,
                LedgerEventRow.entity_type == str(replacement.entity_type),
                LedgerEventRow.sequence_number == replacement.sequence_number,
            )
        ).one()
        row.payload_json = json.dumps(dict(replacement.payload), sort_keys=True)
        row.checksum = replacement.checksum
        store.session.add(row)
        store.session.commit()


class TestIdempotency:
    def test_the_same_append_under_one_key_is_recorded_once(self, ledger):
        first = ledger.append(
            event_type=RECORDED,
            entity_id="cl-11",
            entity_type=COST,
            payload={"amount_cents": 5},
            idempotency_key="charge-42",
        )
        second = ledger.append(
            event_type=RECORDED,
            entity_id="cl-11",
            entity_type=COST,
            payload={"amount_cents": 5},
            idempotency_key="charge-42",
        )

        assert first.was_replay is False
        assert second.was_replay is True
        assert second.event.sequence_number == first.event.sequence_number
        assert len(ledger.events_for("cl-11", COST)) == 1

    def test_reusing_a_key_for_a_different_fact_is_an_error(self, ledger):
        """A repeat is a delivery artefact; a reused key is a caller bug, and
        swallowing it would record neither event."""
        ledger.append(
            event_type=RECORDED,
            entity_id="cl-12",
            entity_type=COST,
            payload={"amount_cents": 5},
            idempotency_key="charge-43",
        )

        with pytest.raises(IdempotencyKeyConflict):
            ledger.append(
                event_type=AMENDED,
                entity_id="cl-12",
                entity_type=COST,
                payload={"amount_cents": 6},
                idempotency_key="charge-43",
            )

    def test_appends_without_a_key_are_never_treated_as_repeats(self, ledger):
        record(ledger, "cl-13", COST, amount_cents=5)
        record(ledger, "cl-13", COST, amount_cents=5)

        assert len(ledger.events_for("cl-13", COST)) == 2


class TestCachedReplay:
    """Replaying a whole stream is the expensive read, so it is the one worth
    caching — and the one where a stale answer is worst, because it is presented
    as the current state of the entity.

    The rule the tests hold it to: a cached replay can never be older than the
    last recorded event.
    """

    @staticmethod
    def counting_sum():
        folds: list[int] = []

        def reduce(state: int, event) -> int:
            folds.append(event.sequence_number)
            return state + event.payload.get("amount_cents", 0)

        return reduce, folds

    def test_a_named_projection_is_served_from_cache(self, store):
        from lore_eden.cache import DictCache

        ledger = Ledger(store, DictCache())
        record(ledger, "cl-20", COST, amount_cents=7)
        reduce, folds = self.counting_sum()

        first = ledger.replay("cl-20", COST, reduce, 0, cache_as="total")
        second = ledger.replay("cl-20", COST, reduce, 0, cache_as="total")

        assert first.state == second.state == 7
        assert len(folds) == 1

    def test_an_append_invalidates_it(self, store):
        from lore_eden.cache import DictCache

        ledger = Ledger(store, DictCache())
        record(ledger, "cl-21", COST, amount_cents=7)
        reduce, folds = self.counting_sum()
        assert ledger.replay("cl-21", COST, reduce, 0, cache_as="total").state == 7

        record(ledger, "cl-21", COST, amount_cents=3)

        assert ledger.replay("cl-21", COST, reduce, 0, cache_as="total").state == 10
        # Both events refolded: the entry went, rather than being patched.
        assert len(folds) == 3

    def test_two_projections_of_one_stream_do_not_answer_each_other(self, store):
        """Why the caller must name the projection. Two reducers over one stream
        produce two states, and a cache keyed on the stream alone would serve
        whichever ran first."""
        from lore_eden.cache import DictCache

        ledger = Ledger(store, DictCache())
        record(ledger, "cl-22", COST, amount_cents=7)

        total = ledger.replay(
            "cl-22", COST, lambda state, event: state + event.payload["amount_cents"], 0,
            cache_as="total",
        )
        count = ledger.replay(
            "cl-22", COST, lambda state, event: state + 1, 0, cache_as="count"
        )

        assert (total.state, count.state) == (7, 1)

    def test_an_unnamed_replay_is_not_cached_rather_than_cached_wrongly(self, store):
        """An anonymous reducer cannot be keyed safely, so it is recomputed.
        Slower, and never wrong."""
        from lore_eden.cache import DictCache

        ledger = Ledger(store, DictCache())
        record(ledger, "cl-23", COST, amount_cents=7)
        reduce, folds = self.counting_sum()

        ledger.replay("cl-23", COST, reduce, 0)
        ledger.replay("cl-23", COST, reduce, 0)

        assert len(folds) == 2

    def test_the_default_ledger_has_no_cache_and_the_same_answers(self, store):
        """NullCache by default, so the cached and uncached paths cannot drift:
        every test in this file above runs on the uncached one."""
        ledger = Ledger(store)
        record(ledger, "cl-24", COST, amount_cents=7)
        record(ledger, "cl-24", COST, amount_cents=3)
        reduce, folds = self.counting_sum()

        first = ledger.replay("cl-24", COST, reduce, 0, cache_as="total")
        second = ledger.replay("cl-24", COST, reduce, 0, cache_as="total")

        assert first.state == second.state == 10
        assert len(folds) == 4


def test_the_readme_snippet_runs() -> None:
    """The README's ledger example, executed.

    The closing audit of the first milestone found a README snippet that had
    never been run and showed a mount that raises. Anything the front page
    claims should be a test somewhere.
    """
    from lore_eden.ledger import EntityType, EventType, Ledger
    from lore_eden.store import InMemoryLedgerStore

    ledger = Ledger(InMemoryLedgerStore())
    ledger.append(
        event_type=EventType("recorded"),
        entity_id="cl-1",
        entity_type=EntityType("cost_line"),
        payload={"amount_cents": 1250},
    )

    total = ledger.replay(
        "cl-1",
        EntityType("cost_line"),
        lambda state, event: state + event.payload["amount_cents"],
        0,
        cache_as="total",
    )

    assert total.state == 1250
    assert ledger.verify_chain("cl-1", EntityType("cost_line")).intact is True

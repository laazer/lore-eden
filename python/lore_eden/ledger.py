"""An append-only event ledger, and replay a host defines the meaning of.

Six loremaker project backlogs each plan to build this — abacus, adam, gaia,
hermes and lexia all carry an "implement immutable ledger" item, and atlas has
already built one. Every one of those READMEs states the goal in nearly the same
words: *all state rebuildable from history*. It is one component, not six.

## What is generic, and what is not

The mechanism came from atlas's `LedgerEvent` and `AtlasLedgerService`, which is
the only implementation in that repo that is genuinely append-only. Two things
were deliberately left behind:

- **`_build_state`.** Atlas's service folds events into service-collection
  state, in about 170 lines that know what a service collection is. That is the
  half a shared ledger cannot have, so :meth:`Ledger.replay` takes the host's
  reducer instead. The ledger knows the order of events and nothing about what
  they mean.
- **The entity vocabulary.** Atlas enumerates its event and entity types.
  Here they are host-owned strings, for the same reason a work item's state is:
  the set belongs to whoever is recording, not to the recorder.

`common/database/change.py` in that repo is *not* the source, despite being the
obvious-looking one. It is a mutable change-log row — its viewset exposes
`update_object` and `delete_object` — with a foreign key to that host's base
model. It records changes; it does not refuse to forget them.

## The hash chain is what makes it evidence

Each event's checksum is computed over the previous event's checksum, so the
events form a chain. Refusing updates stops an honest mistake. The chain is what
survives a dishonest one: an actor who edits a stored payload *and* recomputes
that row's checksum still leaves every later event's checksum derived from the
old value, and :meth:`Ledger.verify_chain` says where it broke.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, NewType, TypeVar

from lore_eden.cache import Cache, NullCache
from lore_eden.timestamps import utcnow

#: What happened. The host's vocabulary — one project's `cost_line_added` is
#: another's `contract_published`, and a shared enum could only be wrong.
EventType = NewType("EventType", str)

#: What it happened to. Host vocabulary for the same reason.
EntityType = NewType("EntityType", str)

#: The checksum an entity's first event chains from. Empty rather than a
#: sentinel string, so a genesis event is recognisable by having nothing behind
#: it rather than by matching a magic value.
GENESIS_CHECKSUM = ""

State = TypeVar("State")


class LedgerError(Exception):
    """Base for every way a ledger refuses."""


class LedgerImmutabilityViolation(LedgerError):
    """Something tried to change or remove an event that had been recorded."""


class LedgerSequenceConflict(LedgerError):
    """Two appends raced for the same sequence number and neither could retry."""


class IdempotencyKeyConflict(LedgerError):
    """A key was reused for a different event.

    Not the same as a repeat: an identical append under the same key returns the
    original event. This is a key that already means something else, which is a
    caller bug worth hearing about rather than a duplicate worth swallowing.
    """


class LedgerChainBroken(LedgerError):
    """Raised by callers that treat a failed verification as fatal.

    :meth:`Ledger.verify_chain` itself returns a report rather than raising: a
    host auditing a hundred entities wants to know about all of them, not the
    first.
    """


@dataclass(frozen=True)
class LedgerEvent:
    """One recorded fact, as a plain value.

    Frozen, which is the cheapest possible statement of the whole point: an
    event that could be edited in memory would be edited in memory.
    """

    event_type: EventType
    entity_id: str
    entity_type: EntityType
    payload: Mapping[str, Any]
    sequence_number: int = 0
    checksum: str = ""
    actor: str = ""
    idempotency_key: str = ""
    occurred_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class AppendResult:
    """The event, and whether it was already there.

    ``was_replay`` distinguishes "recorded" from "recognised". A caller that
    cannot tell them apart either double-counts or cannot report what it did.
    """

    event: LedgerEvent
    was_replay: bool = False


@dataclass(frozen=True)
class ReplayResult:
    """State rebuilt from history, and what it was rebuilt from."""

    state: Any
    events_applied: int
    last_sequence_number: int


@dataclass(frozen=True)
class ChainReport:
    """Whether an entity's events still verify, and where they stop.

    ``broken_at_sequence`` is 0 when the chain is intact, which is a sequence
    number no event has: they start at 1.
    """

    entity_id: str
    entity_type: EntityType
    events_checked: int
    intact: bool
    broken_at_sequence: int = 0


def compute_checksum(
    event_type: EventType,
    entity_id: str,
    entity_type: EntityType,
    sequence_number: int,
    payload: Mapping[str, Any],
    prev_checksum: str = GENESIS_CHECKSUM,
) -> str:
    """The canonical SHA-256 for one event, chained to its predecessor.

    Canonical means sorted keys and no incidental whitespace, so two payloads
    that differ only in dict ordering hash the same — otherwise a round trip
    through JSON could break a chain that nothing tampered with.

    ``occurred_at``, ``actor`` and ``idempotency_key`` are deliberately outside
    the hash. They describe the recording, not the fact recorded, and including
    them would make a legitimate backfill indistinguishable from tampering.
    """
    canonical = json.dumps(
        {
            "event_type": str(event_type),
            "entity_id": entity_id,
            "entity_type": str(entity_type),
            "sequence_number": sequence_number,
            "payload": dict(payload),
            "prev_checksum": prev_checksum,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Ledger:
    """Append, read back, replay, and check that nothing moved.

    The store is a protocol (:class:`lore_eden.store.protocols.LedgerStore`), so
    a host with Postgres and its own ORM implements four methods and installs no
    extra. There is no update or delete anywhere in that protocol: the surface
    itself is the immutability guarantee, rather than a rule the implementations
    are each asked to remember.
    """

    #: One retry, then give up. A second racer is normal; a third means the
    #: contention is structural and looping would only hide it.
    MAX_APPEND_ATTEMPTS = 2

    def __init__(self, store: Any, cache: Cache | None = None) -> None:
        self._store = store
        # NullCache by default, so a host that wants none gets behaviour
        # identical to one that has one. That equivalence is what lets the
        # cached and uncached paths share every test below.
        self._cache = cache if cache is not None else NullCache()

    def append(
        self,
        *,
        event_type: EventType,
        entity_id: str,
        entity_type: EntityType,
        payload: Mapping[str, Any],
        actor: str = "",
        idempotency_key: str = "",
    ) -> AppendResult:
        """Record one event at the end of an entity's stream.

        Sequence numbers are assigned here rather than by the caller, because a
        caller that picks its own has to know what the last one was, and two
        callers that both know are two callers that both pick the same.
        """
        if idempotency_key:
            seen = self._store.event_by_idempotency_key(idempotency_key)
            if seen is not None:
                return AppendResult(self._matched(seen, event_type, entity_id, entity_type, payload), True)

        for attempt in range(self.MAX_APPEND_ATTEMPTS):
            last = self._store.last_event(entity_id, entity_type)
            sequence_number = (last.sequence_number if last is not None else 0) + 1
            prev_checksum = last.checksum if last is not None else GENESIS_CHECKSUM
            candidate = LedgerEvent(
                event_type=event_type,
                entity_id=entity_id,
                entity_type=entity_type,
                payload=dict(payload),
                sequence_number=sequence_number,
                checksum=compute_checksum(
                    event_type, entity_id, entity_type, sequence_number, payload, prev_checksum
                ),
                actor=actor,
                idempotency_key=idempotency_key,
            )
            try:
                stored = self._store.append_event(candidate)
                # Drop every cached replay of this stream. Derived from the tag
                # the read declares, so a new cached projection is covered the
                # moment it exists — there is no list here to update.
                self._cache.invalidate_tags(frozenset({self.stream_tag(entity_id, entity_type)}))
                return AppendResult(stored, False)
            except LedgerSequenceConflict:
                # Someone else took this sequence number between the read and
                # the write. Re-read and rebuild rather than incrementing: the
                # checksum chains to whatever actually landed, not to what was
                # there when this attempt started.
                if idempotency_key:
                    seen = self._store.event_by_idempotency_key(idempotency_key)
                    if seen is not None:
                        return AppendResult(
                            self._matched(seen, event_type, entity_id, entity_type, payload), True
                        )
                if attempt == self.MAX_APPEND_ATTEMPTS - 1:
                    raise
        raise LedgerSequenceConflict(  # pragma: no cover - the loop above always returns or raises
            f"could not place an event on {entity_type} '{entity_id}'"
        )

    @staticmethod
    def _matched(
        seen: LedgerEvent,
        event_type: EventType,
        entity_id: str,
        entity_type: EntityType,
        payload: Mapping[str, Any],
    ) -> LedgerEvent:
        """The stored event, if the key was reused for the same fact."""
        same = (
            seen.event_type == event_type
            and seen.entity_id == entity_id
            and seen.entity_type == entity_type
            and dict(seen.payload) == dict(payload)
        )
        if not same:
            raise IdempotencyKeyConflict(
                f"idempotency_key '{seen.idempotency_key}' already recorded "
                f"{seen.event_type} on {seen.entity_type} '{seen.entity_id}'"
            )
        return seen

    @staticmethod
    def stream_tag(entity_id: str, entity_type: EntityType) -> str:
        """What a cached replay of this stream derives from.

        One tag per stream, declared here so the read and the write agree by
        construction: :meth:`append` invalidates this exact tag, and
        :meth:`replay` stores under it. Neither has a list to keep in step.
        """
        return f"ledger:{entity_type}:{entity_id}"

    def events_for(self, entity_id: str, entity_type: EntityType) -> Sequence[LedgerEvent]:
        """Every event for one entity, in the order it happened."""
        return self._store.events_for(entity_id, entity_type)

    def replay(
        self,
        entity_id: str,
        entity_type: EntityType,
        reducer: Callable[[State, LedgerEvent], State],
        initial: State,
        cache_as: str = "",
    ) -> ReplayResult:
        """Rebuild state by folding the host's reducer over the stream.

        Deterministic by construction: the events come back ordered by sequence
        number, and nothing here consults a clock, a random source or anything
        outside the fold. The same ledger replayed twice returns equal state,
        and a test asserts it rather than the docstring being the only claim.

        Replaying the whole stream is the expensive read, so the result is
        cacheable — but only when the caller *names* the projection with
        ``cache_as``. Two reducers over one stream produce two different states,
        and a cache keyed on the stream alone would serve one where the other
        was asked for. An anonymous reducer cannot be keyed safely, so an
        unnamed replay simply is not cached rather than being cached wrongly.

        Any append to the stream drops the entry, so a cached replay can never
        be older than the last recorded event.
        """
        if not cache_as:
            return self._replay_now(entity_id, entity_type, reducer, initial)

        key = f"ledger-replay:{cache_as}:{entity_type}:{entity_id}"
        found = self._cache.get(key)
        if found.found:
            return found.value
        result = self._replay_now(entity_id, entity_type, reducer, initial)
        self._cache.set(key, result, frozenset({self.stream_tag(entity_id, entity_type)}))
        return result

    def _replay_now(
        self,
        entity_id: str,
        entity_type: EntityType,
        reducer: Callable[[State, LedgerEvent], State],
        initial: State,
    ) -> ReplayResult:
        """The fold itself, with nothing between it and the stored events."""
        events = list(self._store.events_for(entity_id, entity_type))
        state = initial
        for event in events:
            state = reducer(state, event)
        last_sequence = events[-1].sequence_number if events else 0
        return ReplayResult(state=state, events_applied=len(events), last_sequence_number=last_sequence)

    def verify_chain(self, entity_id: str, entity_type: EntityType) -> ChainReport:
        """Recompute every checksum and report the first that disagrees.

        A report rather than an exception: a host auditing a hundred entities
        wants all the answers, not the first failure. Callers that want it fatal
        raise :class:`LedgerChainBroken` themselves.
        """
        events = list(self._store.events_for(entity_id, entity_type))
        prev_checksum = GENESIS_CHECKSUM
        for event in events:
            expected = compute_checksum(
                event.event_type,
                event.entity_id,
                event.entity_type,
                event.sequence_number,
                event.payload,
                prev_checksum,
            )
            if expected != event.checksum:
                return ChainReport(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    events_checked=len(events),
                    intact=False,
                    broken_at_sequence=event.sequence_number,
                )
            prev_checksum = event.checksum
        return ChainReport(
            entity_id=entity_id,
            entity_type=entity_type,
            events_checked=len(events),
            intact=True,
        )


def replace_payload_for_testing(event: LedgerEvent, payload: Mapping[str, Any]) -> LedgerEvent:
    """A tampered copy, for tests that need to prove the chain notices.

    Exported deliberately rather than left to each test to improvise with
    ``dataclasses.replace``: the tampering test is the one that proves the
    checksum is worth computing, and it should be obvious what it is doing.
    """
    return replace(event, payload=dict(payload))

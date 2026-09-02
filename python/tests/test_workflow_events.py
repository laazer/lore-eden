"""In-process publish/subscribe."""

from __future__ import annotations

import logging
import threading

from lore_eden.workflow import EventBus


def test_subscribers_receive_published_events():
    bus: EventBus[str] = EventBus()
    seen: list[str] = []
    bus.subscribe(seen.append)

    bus.publish("stage_completed")

    assert seen == ["stage_completed"]


def test_every_subscriber_receives_the_event_in_registration_order():
    bus: EventBus[str] = EventBus()
    order: list[str] = []
    bus.subscribe(lambda event: order.append(f"first:{event}"))
    bus.subscribe(lambda event: order.append(f"second:{event}"))

    bus.publish("x")

    assert order == ["first:x", "second:x"]


def test_unsubscribing_stops_delivery():
    """Returning the unsubscribe is what keeps a request-scoped handler from
    outliving the thing that registered it."""
    bus: EventBus[str] = EventBus()
    seen: list[str] = []
    unsubscribe = bus.subscribe(seen.append)

    bus.publish("one")
    unsubscribe()
    bus.publish("two")

    assert seen == ["one"]


def test_unsubscribing_twice_is_harmless():
    bus: EventBus[str] = EventBus()
    unsubscribe = bus.subscribe(lambda event: None)

    unsubscribe()
    unsubscribe()


def test_one_failing_subscriber_does_not_rob_the_others(caplog):
    bus: EventBus[str] = EventBus()
    seen: list[str] = []

    def explodes(event: str) -> None:
        raise RuntimeError("subscriber is broken")

    bus.subscribe(explodes)
    bus.subscribe(seen.append)

    with caplog.at_level(logging.ERROR):
        bus.publish("x")

    assert seen == ["x"]
    # Logged with its traceback, not swallowed: a subscriber that silently stops
    # working is indistinguishable from one with nothing to do.
    assert "subscriber is broken" in caplog.text


def test_publishing_from_several_threads_delivers_everything():
    bus: EventBus[int] = EventBus()
    seen: list[int] = []
    bus.subscribe(seen.append)

    threads = [threading.Thread(target=bus.publish, args=(n,)) for n in range(25)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(seen) == list(range(25))


def test_subscribing_during_delivery_does_not_deadlock():
    """Delivery happens off a copy of the list, so a handler that registers
    another handler cannot block on the lock it is already under."""
    bus: EventBus[str] = EventBus()
    seen: list[str] = []

    def subscribes_more(event: str) -> None:
        bus.subscribe(seen.append)

    bus.subscribe(subscribes_more)
    bus.publish("first")
    bus.publish("second")

    assert seen == ["second"]

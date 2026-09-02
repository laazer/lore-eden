"""In-process publish/subscribe for workflow events.

Deliberately not a persistence layer. The version this came from wrote each
event to a database inside `publish`, which meant the bus could not be used
without that schema, and a subscriber could not be notified about anything the
host had not chosen to store.

Here the host persists — if it wants to — and then publishes. That ordering is
the useful one anyway: a subscriber that reacts to an event should be able to
read it back.

Delivery is synchronous and under a lock, so subscribers observe events in the
order they were published and never two at once. A slow subscriber therefore
holds up the publisher; that is the trade for ordering, and a subscriber with
real work to do should hand it to a queue.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

EventT = TypeVar("EventT")

Subscriber = Callable[[EventT], None]


class EventBus(Generic[EventT]):
    """Notifies subscribers, in order, one at a time."""

    def __init__(self) -> None:
        self._subscribers: list[Subscriber[EventT]] = []
        self._lock = threading.Lock()

    def subscribe(self, handler: Subscriber[EventT]) -> Callable[[], None]:
        """Register ``handler``. Returns a callable that unsubscribes it.

        Returning the unsubscribe is what makes the bus usable from a test or a
        request-scoped component without leaking a handler that outlives them.
        """
        with self._lock:
            self._subscribers.append(handler)

        def unsubscribe() -> None:
            with self._lock:
                if handler in self._subscribers:
                    self._subscribers.remove(handler)

        return unsubscribe

    def publish(self, event: EventT) -> None:
        """Deliver ``event`` to every subscriber.

        One subscriber raising does not rob the others of the event, nor the
        publisher of its return — but the failure is logged with its traceback
        rather than swallowed, because a subscriber that silently stops working
        is indistinguishable from one that has nothing to do.
        """
        with self._lock:
            subscribers = list(self._subscribers)
        for handler in subscribers:
            try:
                handler(event)
            except Exception:
                logger.exception("event subscriber %r failed handling %r", handler, event)

from __future__ import annotations

import queue
import threading
from datetime import UTC
from datetime import datetime
from typing import Any

_Subscriber = queue.Queue[dict[str, Any]]


class EventBroker:
    """Thread-safe, in-process pub/sub used by the dashboard SSE feed.

    Producers call :meth:`publish` from any thread (the API sync endpoints
    are threadpool workers); the SSE endpoint subscribes with a blocking
    :class:`queue.Queue` it drains asynchronously. When a subscriber is
    slower than the producers the oldest undelivered event is dropped so a
    stalled client can never grow memory unboundedly (OT-008 spirit).
    """

    def __init__(self, maxlen: int = 50) -> None:
        self._maxlen = maxlen
        self._lock = threading.Lock()
        self._subscribers: set[_Subscriber] = set()

    def subscribe(self) -> _Subscriber:
        sub: _Subscriber = queue.Queue(maxsize=self._maxlen)
        with self._lock:
            self._subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: _Subscriber) -> None:
        with self._lock:
            self._subscribers.discard(sub)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(self, event: dict[str, Any]) -> int:
        """Synchronous, thread-safe publish; returns subscribers notified."""
        with self._lock:
            subscribers = list(self._subscribers)
        delivered = 0
        for sub in subscribers:
            try:
                sub.put_nowait(event)
                delivered += 1
            except queue.Full:
                try:
                    sub.get_nowait()
                except queue.Empty:
                    pass
                try:
                    sub.put_nowait(event)
                    delivered += 1
                except queue.Full:
                    pass
        return delivered


_broker: EventBroker | None = None


def get_broker() -> EventBroker:
    global _broker
    if _broker is None:
        _broker = EventBroker()
    return _broker


def reset_broker() -> None:
    global _broker
    _broker = None


def publish_event(event_type: str, data: dict[str, Any] | None = None) -> int:
    """Synchronously publish a dashboard event from any thread."""
    event: dict[str, Any] = {
        "type": event_type,
        "data": data or {},
        "timestamp": datetime.now(UTC).isoformat(),
    }
    return get_broker().publish(event)

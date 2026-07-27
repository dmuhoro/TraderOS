from __future__ import annotations

import logging

from traderos.domain.ports import Event
from traderos.domain.ports import EventBusPort
from traderos.domain.ports import EventHandler

# backward compat: EventBus was the original ABC name
EventBus = EventBusPort


class InMemoryEventBus(EventBusPort):
    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = {}
        self._log = logging.getLogger(__name__)

    def publish(self, event: Event) -> None:
        handlers = self._subscribers.get(event.event_type, [])
        for handler in handlers:
            try:
                handler(event)
            except Exception:  # noqa: BLE001
                self._log.exception("Event handler failed for %s", event.event_type)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        handlers = self._subscribers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

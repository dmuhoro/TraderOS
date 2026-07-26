from __future__ import annotations

import uuid
from abc import ABC
from abc import abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Event:
    event_type: str
    payload: dict[str, Any]
    event_id: uuid.UUID = field(default_factory=uuid.uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


EventHandler = Callable[[Event], None]


class EventBus(ABC):
    @abstractmethod
    def publish(self, event: Event) -> None: ...

    @abstractmethod
    def subscribe(self, event_type: str, handler: EventHandler) -> None: ...

    @abstractmethod
    def unsubscribe(self, event_type: str, handler: EventHandler) -> None: ...


class InMemoryEventBus(EventBus):
    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = {}

    def publish(self, event: Event) -> None:
        handlers = self._subscribers.get(event.event_type, [])
        for handler in handlers:
            handler(event)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        handlers = self._subscribers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

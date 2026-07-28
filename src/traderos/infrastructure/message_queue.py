from __future__ import annotations

import abc
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger(__name__)

MESSAGE_QUEUE_URL = os.getenv("MESSAGE_QUEUE_URL", "")


@dataclass
class Message:
    topic: str
    data: dict[str, Any]
    message_id: str = ""


MessageHandler = Callable[[Message], None]


class MessageQueue(abc.ABC):
    @abc.abstractmethod
    def publish(self, topic: str, data: dict[str, Any]) -> None: ...

    @abc.abstractmethod
    def subscribe(self, topic: str, handler: MessageHandler) -> None: ...

    @abc.abstractmethod
    def unsubscribe(self, topic: str, handler: MessageHandler) -> None: ...

    @abc.abstractmethod
    def start(self) -> None: ...

    @abc.abstractmethod
    def stop(self) -> None: ...


class InMemoryMessageQueue(MessageQueue):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[MessageHandler]] = {}
        self._running = False
        self._buffer: list[Message] = []
        self._dispatch_thread: threading.Thread | None = None

    def publish(self, topic: str, data: dict[str, Any]) -> None:
        msg = Message(topic=topic, data=data)
        buffer = False
        with self._lock:
            if self._running:
                self._buffer.append(msg)
                buffer = True
        if not buffer:
            self._dispatch(msg)

    def subscribe(self, topic: str, handler: MessageHandler) -> None:
        with self._lock:
            if topic not in self._subscribers:
                self._subscribers[topic] = []
            self._subscribers[topic].append(handler)

    def unsubscribe(self, topic: str, handler: MessageHandler) -> None:
        with self._lock:
            handlers = self._subscribers.get(topic, [])
            if handler in handlers:
                handlers.remove(handler)

    def start(self) -> None:
        with self._lock:
            self._running = True
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop, daemon=True, name="mq-dispatch"
        )
        self._dispatch_thread.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False
        if self._dispatch_thread and self._dispatch_thread.is_alive():
            self._dispatch_thread.join(timeout=5)

    def _dispatch_loop(self) -> None:
        while self._running:
            msg: Message | None = None
            with self._lock:
                if self._buffer:
                    msg = self._buffer.pop(0)
            if msg is not None:
                self._dispatch(msg)
            else:
                time.sleep(0.01)

    def _dispatch(self, msg: Message) -> None:
        handlers: list[MessageHandler] = []
        with self._lock:
            handlers.extend(self._subscribers.get(msg.topic, []))
            handlers.extend(self._subscribers.get("*", []))
        for handler in handlers:
            try:
                handler(msg)
            except Exception:
                _LOGGER.exception("Message handler failed for topic %s", msg.topic)


class RedisMessageQueue(MessageQueue):
    def __init__(self, url: str = "") -> None:
        self._url = url or MESSAGE_QUEUE_URL
        self._pubsub: Any = None
        self._redis: Any = None
        self._running = False
        self._handlers: dict[str, list[MessageHandler]] = {}
        self._dispatch_thread: threading.Thread | None = None

    def _connect(self) -> None:
        try:
            import redis as _r

            self._redis = _r.from_url(self._url)
            self._redis.ping()
            self._pubsub = self._redis.pubsub()
        except ImportError as err:
            raise ImportError(
                "redis-py is required. Install with: pip install traderos[redis]"
            ) from err

    def publish(self, topic: str, data: dict[str, Any]) -> None:
        if self._redis is None:
            self._connect()
        if self._redis is not None:
            payload = json.dumps(data)
            self._redis.publish(topic, payload)

    def subscribe(self, topic: str, handler: MessageHandler) -> None:
        if self._redis is None:
            self._connect()
        if topic not in self._handlers:
            self._handlers[topic] = []
            self._pubsub.subscribe(**{topic: self._on_message})
        self._handlers[topic].append(handler)

    def unsubscribe(self, topic: str, handler: MessageHandler) -> None:
        handlers = self._handlers.get(topic, [])
        if handler in handlers:
            handlers.remove(handler)
        if not handlers:
            self._pubsub.unsubscribe(topic)
            self._handlers.pop(topic, None)

    def start(self) -> None:
        self._running = True
        self._dispatch_thread = threading.Thread(
            target=self._listen_loop, daemon=True, name="redis-mq"
        )
        self._dispatch_thread.start()

    def stop(self) -> None:
        self._running = False
        if self._pubsub:
            self._pubsub.close()
        if self._redis:
            self._redis.close()

    def _on_message(self, raw: Any) -> None:
        if raw["type"] != "message":
            return
        topic = raw["channel"].decode() if isinstance(raw["channel"], bytes) else raw["channel"]
        data = json.loads(raw["data"])
        msg = Message(topic=topic, data=data)
        with threading.Lock():
            handlers = list(self._handlers.get(topic, []))
        for handler in handlers:
            try:
                handler(msg)
            except Exception:
                _LOGGER.exception("Redis handler failed for topic %s", topic)

    def _listen_loop(self) -> None:
        while self._running and self._pubsub:
            self._pubsub.get_message(timeout=1.0)


def create_message_queue(url: str = "") -> MessageQueue:
    u = url or MESSAGE_QUEUE_URL
    if u.startswith("redis://"):
        return RedisMessageQueue(u)
    return InMemoryMessageQueue()

from __future__ import annotations

import sys
import time
from types import ModuleType

import pytest

from traderos.infrastructure.message_queue import InMemoryMessageQueue
from traderos.infrastructure.message_queue import RedisMessageQueue
from traderos.infrastructure.message_queue import create_message_queue


@pytest.fixture
def fake_redis(monkeypatch):
    """Register a fake `redis` module so RedisMessageQueue._connect works."""

    class FakePubSub:
        def __init__(self) -> None:
            self.subscribed: dict = {}
            self.unsubscribed: list[str] = []

        def subscribe(self, **kwargs) -> None:
            self.subscribed.update(kwargs)

        def unsubscribe(self, *topics) -> None:
            self.unsubscribed.extend(topics)

        def get_message(self, timeout=0.0) -> None:
            return None

        def close(self) -> None:
            return None

    class FakeRedis:
        def __init__(self) -> None:
            self.pubsub_client = FakePubSub()
            self.published: list[tuple[str, str]] = []
            self.closed = False

        def from_url(self, url):
            self.url = url
            return self

        def ping(self) -> None:
            return None

        def pubsub(self) -> FakePubSub:
            return self.pubsub_client

        def publish(self, topic, payload):
            self.published.append((topic, payload))

        def close(self) -> None:
            self.closed = True

    fake = FakeRedis()
    module = ModuleType("redis")
    module.from_url = fake.from_url
    module.__dict__["FakeRedis"] = fake
    monkeypatch.setitem(sys.modules, "redis", module)
    return fake


class TestInMemoryMessageQueue:
    def test_publish_subscribe(self):
        mq = InMemoryMessageQueue()
        received: list[str] = []

        def handler(msg):
            received.append(msg.data.get("value"))

        mq.subscribe("test", handler)
        mq.publish("test", {"value": "hello"})
        assert received == ["hello"]

    def test_wildcard_subscriber(self):
        mq = InMemoryMessageQueue()
        received: list[tuple[str, str]] = []

        def handler(msg):
            received.append((msg.topic, msg.data.get("value")))

        mq.subscribe("*", handler)
        mq.publish("topic.a", {"value": "a"})
        mq.publish("topic.b", {"value": "b"})
        assert ("topic.a", "a") in received
        assert ("topic.b", "b") in received

    def test_unsubscribe(self):
        mq = InMemoryMessageQueue()
        calls: list[int] = []

        def handler(msg):
            calls.append(1)

        mq.subscribe("test", handler)
        mq.publish("test", {})
        assert len(calls) == 1
        mq.unsubscribe("test", handler)
        mq.publish("test", {})
        assert len(calls) == 1

    def test_multiple_subscribers(self):
        mq = InMemoryMessageQueue()
        c1: list[str] = []
        c2: list[str] = []

        def h1(msg):
            c1.append("h1")

        def h2(msg):
            c2.append("h2")

        mq.subscribe("test", h1)
        mq.subscribe("test", h2)
        mq.publish("test", {})
        assert c1 == ["h1"]
        assert c2 == ["h2"]

    def test_start_stop(self):
        mq = InMemoryMessageQueue()
        mq.start()
        assert mq._running is True
        mq.stop()
        assert mq._running is False

    def test_buffered_dispatch(self):
        mq = InMemoryMessageQueue()
        mq.start()
        received: list[str] = []

        def handler(msg):
            received.append(msg.data.get("value"))

        mq.subscribe("test", handler)
        mq.publish("test", {"value": "buffered"})
        time.sleep(0.1)
        assert "buffered" in received
        mq.stop()

    def test_create_in_memory_by_default(self):
        mq = create_message_queue("")
        assert isinstance(mq, InMemoryMessageQueue)

    def test_create_redis_with_url(self):
        mq = create_message_queue("redis://localhost:6379")
        from traderos.infrastructure.message_queue import RedisMessageQueue

        assert isinstance(mq, RedisMessageQueue)

    def test_handler_exception_does_not_crash(self):
        mq = InMemoryMessageQueue()
        errors: list[str] = []

        def bad_handler(msg):
            raise ValueError("oops")

        def good_handler(msg):
            errors.append("ok")

        mq.subscribe("test", bad_handler)
        mq.subscribe("test", good_handler)
        mq.publish("test", {})
        assert errors == ["ok"]


class TestRedisMessageQueue:
    def test_publish_connects_and_publishes_json(self, fake_redis):
        mq = RedisMessageQueue("redis://localhost:6379")
        mq.publish("signals", {"value": 1})
        assert fake_redis.published == [("signals", '{"value": 1}')]

    def test_subscribe_registers_handler(self, fake_redis):
        mq = RedisMessageQueue("redis://localhost:6379")
        handler = lambda msg: None  # noqa: E731
        mq.subscribe("signals", handler)
        assert list(mq._handlers["signals"]) == [handler]
        assert "signals" in mq._pubsub.subscribed

    def test_subscribe_multiple_handlers_single_subscription(self, fake_redis):
        mq = RedisMessageQueue("redis://localhost:6379")
        h1 = lambda msg: None  # noqa: E731
        h2 = lambda msg: None  # noqa: E731
        mq.subscribe("signals", h1)
        mq.subscribe("signals", h2)
        assert list(mq._pubsub.subscribed) == ["signals"]
        assert mq._handlers["signals"] == [h1, h2]

    def test_unsubscribe_removes_handler_then_unsubscribes(self, fake_redis):
        mq = RedisMessageQueue("redis://localhost:6379")
        h1 = lambda msg: None  # noqa: E731
        mq.subscribe("signals", h1)
        mq.unsubscribe("signals", h1)
        assert "signals" not in mq._handlers
        assert "signals" in mq._pubsub.unsubscribed

    def test_unsubscribe_keeps_subscription_when_handlers_remain(self, fake_redis):
        mq = RedisMessageQueue("redis://localhost:6379")
        h1 = lambda msg: None  # noqa: E731
        h2 = lambda msg: None  # noqa: E731
        mq.subscribe("signals", h1)
        mq.subscribe("signals", h2)
        mq.unsubscribe("signals", h1)
        assert mq._handlers["signals"] == [h2]
        assert mq._pubsub.unsubscribed == []

    def test_on_message_dispatches_to_handler(self, fake_redis):
        mq = RedisMessageQueue("redis://localhost:6379")
        received: list[dict] = []

        def handler(msg):
            received.append(msg.data)

        mq.subscribe("signals", handler)
        mq._on_message({"type": "message", "channel": b"signals", "data": '{"value": 2}'})
        assert received == [{"value": 2}]

    def test_on_message_ignores_non_message_events(self, fake_redis):
        mq = RedisMessageQueue("redis://localhost:6379")
        mq._on_message({"type": "subscribe", "channel": b"signals", "data": 1})

    def test_on_message_handler_error_is_isolated(self, fake_redis):
        mq = RedisMessageQueue("redis://localhost:6379")
        received: list[dict] = []

        def bad(msg):
            raise ValueError("boom")

        def good(msg):
            received.append(msg.data)

        mq.subscribe("signals", bad)
        mq.subscribe("signals", good)
        mq._on_message({"type": "message", "channel": b"signals", "data": '{"value": 3}'})
        assert received == [{"value": 3}]

    def test_start_stop(self, fake_redis):
        mq = RedisMessageQueue("redis://localhost:6379")
        mq.subscribe("signals", lambda msg: None)
        mq.start()
        assert mq._running is True
        mq.stop()
        assert mq._running is False
        assert fake_redis.closed is True

    def test_connect_requires_redis_package(self, monkeypatch):
        def no_redis(_url):
            raise ImportError("redis-py is required")

        module = ModuleType("redis")
        module.from_url = no_redis
        monkeypatch.setitem(sys.modules, "redis", module)
        mq = RedisMessageQueue("redis://localhost:6379")
        with pytest.raises(ImportError):
            mq._connect()

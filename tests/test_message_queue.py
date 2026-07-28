from __future__ import annotations

import time

from traderos.infrastructure.message_queue import InMemoryMessageQueue
from traderos.infrastructure.message_queue import create_message_queue


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

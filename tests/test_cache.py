from __future__ import annotations

import sys
import time
from types import ModuleType

import pytest

from traderos.infrastructure.cache import InMemoryCache
from traderos.infrastructure.cache import RedisCache
from traderos.infrastructure.cache import create_cache


class TestInMemoryCache:
    def test_get_set(self):
        cache: InMemoryCache[str] = InMemoryCache()
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_get_missing(self):
        cache: InMemoryCache[str] = InMemoryCache()
        assert cache.get("missing") is None

    def test_delete(self):
        cache: InMemoryCache[str] = InMemoryCache()
        cache.set("key1", "value1")
        cache.delete("key1")
        assert cache.get("key1") is None

    def test_clear(self):
        cache: InMemoryCache[str] = InMemoryCache()
        cache.set("a", "1")
        cache.set("b", "2")
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None

    def test_has(self):
        cache: InMemoryCache[str] = InMemoryCache()
        assert cache.has("key") is False
        cache.set("key", "val")
        assert cache.has("key") is True

    def test_ttl_expiry(self):
        cache: InMemoryCache[str] = InMemoryCache(default_ttl=0)
        cache.set("key", "val")
        time.sleep(0.01)
        assert cache.get("key") is None

    def test_custom_ttl(self):
        cache: InMemoryCache[str] = InMemoryCache(default_ttl=100)
        cache.set("key", "val", ttl=0)
        time.sleep(0.01)
        assert cache.get("key") is None

    def test_eviction_when_full(self):
        cache: InMemoryCache[str] = InMemoryCache(max_size=2, default_ttl=100)
        cache.set("a", "1")
        cache.set("b", "2")
        cache.set("c", "3")
        assert cache.size <= 2

    def test_hit_counting(self):
        cache: InMemoryCache[str] = InMemoryCache()
        cache.set("key", "val")
        cache.get("key")
        cache.get("key")
        stats = cache.stats
        assert stats["total_hits"] >= 2

    def test_create_in_memory(self):
        c = create_cache("")
        assert isinstance(c, InMemoryCache)

    def test_create_redis(self):
        c = create_cache("redis://localhost:6379/0")
        from traderos.infrastructure.cache import RedisCache

        assert isinstance(c, RedisCache)

    def test_complex_values(self):
        cache: InMemoryCache[dict] = InMemoryCache()
        data = {"name": "test", "values": [1, 2, 3]}
        cache.set("complex", data)
        assert cache.get("complex") == data

    def test_stale_entry_not_returned(self):
        cache: InMemoryCache[str] = InMemoryCache(default_ttl=0)
        cache.set("stale", "val")
        time.sleep(0.01)
        assert cache.get("expired") is None

    def test_has_returns_false_and_cleans_expired(self):
        cache: InMemoryCache[str] = InMemoryCache(default_ttl=0)
        cache.set("stale", "val")
        time.sleep(0.01)
        assert cache.has("stale") is False
        assert cache.get("stale") is None


class _FakeRedisClient:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def setex(self, key: str, ttl: int, payload: str) -> bool:
        self.data[key] = payload
        return True

    def delete(self, key: str) -> int:
        return 1 if self.data.pop(key, None) is not None else 0

    def flushdb(self) -> bool:
        self.data.clear()
        return True

    def exists(self, key: str) -> int:
        return 1 if key in self.data else 0


@pytest.fixture
def _fake_redis(monkeypatch: pytest.MonkeyPatch):
    clients: list[_FakeRedisClient] = []

    def _from_url(url: str) -> _FakeRedisClient:
        client = _FakeRedisClient()
        clients.append(client)
        return client

    mod = ModuleType("redis")
    mod.from_url = _from_url
    monkeypatch.setitem(sys.modules, "redis", mod)
    return clients


class TestRedisCache:
    def test_get_missing(self, _fake_redis):
        cache: RedisCache[str] = RedisCache("redis://localhost:6379/0")
        assert cache.get("nope") is None

    def test_set_get_roundtrip(self, _fake_redis):
        cache: RedisCache[dict] = RedisCache("redis://localhost:6379/0")
        cache.set("k", {"a": 1})
        assert cache.get("k") == {"a": 1}

    def test_set_with_custom_ttl(self, _fake_redis):
        cache: RedisCache[str] = RedisCache("redis://localhost:6379/0")
        cache.set("k", "v", ttl=60)
        assert _fake_redis[0].data["k"] == '"v"'

    def test_delete(self, _fake_redis):
        cache: RedisCache[str] = RedisCache("redis://localhost:6379/0")
        cache.set("k", "v")
        cache.delete("k")
        assert cache.get("k") is None

    def test_clear(self, _fake_redis):
        cache: RedisCache[str] = RedisCache("redis://localhost:6379/0")
        cache.set("a", "1")
        cache.set("b", "2")
        cache.clear()
        assert cache.has("a") is False
        assert cache.has("b") is False

    def test_has(self, _fake_redis):
        cache: RedisCache[str] = RedisCache("redis://localhost:6379/0")
        assert cache.has("k") is False
        cache.set("k", "v")
        assert cache.has("k") is True

    def test_import_error_raises_helpful_message(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setitem(sys.modules, "redis", None)
        cache: RedisCache[str] = RedisCache("redis://localhost:6379/0")
        with pytest.raises(ImportError, match="redis-py required"):
            cache.get("k")

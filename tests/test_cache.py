from __future__ import annotations

import time

from traderos.infrastructure.cache import InMemoryCache
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

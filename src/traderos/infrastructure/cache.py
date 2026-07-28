from __future__ import annotations

import abc
import json
import os
import threading
import time
from typing import Any
from typing import Generic
from typing import TypeVar

T = TypeVar("T")
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "300"))  # 5 min default
CACHE_MAX_SIZE = int(os.getenv("CACHE_MAX_SIZE", "10000"))


class Cache(abc.ABC, Generic[T]):
    @abc.abstractmethod
    def get(self, key: str) -> T | None: ...

    @abc.abstractmethod
    def set(self, key: str, value: T, ttl: int | None = None) -> None: ...

    @abc.abstractmethod
    def delete(self, key: str) -> None: ...

    @abc.abstractmethod
    def clear(self) -> None: ...

    @abc.abstractmethod
    def has(self, key: str) -> bool: ...


class InMemoryCache(Cache[T]):
    def __init__(self, max_size: int = CACHE_MAX_SIZE, default_ttl: int = CACHE_TTL) -> None:
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._lock = threading.Lock()
        self._data: dict[str, _CacheEntry[T]] = {}

    def get(self, key: str) -> T | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if entry.is_expired:
                del self._data[key]
                return None
            entry.hits += 1
            return entry.value

    def set(self, key: str, value: T, ttl: int | None = None) -> None:
        with self._lock:
            if len(self._data) >= self._max_size and key not in self._data:
                self._evict()
            self._data[key] = _CacheEntry(
                value=value,
                expires_at=time.monotonic() + (ttl if ttl is not None else self._default_ttl),
            )

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def has(self, key: str) -> bool:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return False
            if entry.is_expired:
                del self._data[key]
                return False
            return True

    def _evict(self) -> None:
        oldest = min(self._data.keys(), key=lambda k: self._data[k].expires_at)
        del self._data[oldest]

    @property
    def size(self) -> int:
        return len(self._data)

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            total_hits = sum(e.hits for e in self._data.values())
            expired = sum(1 for e in self._data.values() if e.is_expired)
            return {
                "size": len(self._data),
                "max_size": self._max_size,
                "total_hits": total_hits,
                "expired_entries": expired,
            }


class _CacheEntry(Generic[T]):
    def __init__(self, value: T, expires_at: float) -> None:
        self.value = value
        self.expires_at = expires_at
        self.hits = 0

    @property
    def is_expired(self) -> bool:
        return time.monotonic() > self.expires_at


class RedisCache(Cache[T]):
    def __init__(self, url: str = "", default_ttl: int = CACHE_TTL) -> None:
        self._url = url or os.getenv("MESSAGE_QUEUE_URL", "")
        self._default_ttl = default_ttl
        self._redis: Any = None

    def _connect(self) -> None:
        if self._redis is None:
            try:
                import redis as _r

                self._redis = _r.from_url(self._url)
            except ImportError as err:
                raise ImportError(
                    "redis-py required. Install with: pip install traderos[redis]"
                ) from err

    def get(self, key: str) -> T | None:
        self._connect()
        val = self._redis.get(key)
        if val is None:
            return None
        return json.loads(val)

    def set(self, key: str, value: T, ttl: int | None = None) -> None:
        self._connect()
        payload = json.dumps(value, default=str)
        self._redis.setex(key, ttl or self._default_ttl, payload)

    def delete(self, key: str) -> None:
        self._connect()
        self._redis.delete(key)

    def clear(self) -> None:
        self._connect()
        self._redis.flushdb()

    def has(self, key: str) -> bool:
        self._connect()
        return bool(self._redis.exists(key))


def create_cache(url: str = "") -> Cache[Any]:
    u = url or os.getenv("REDIS_URL", "")
    if u.startswith("redis://"):
        return RedisCache(u)
    return InMemoryCache()

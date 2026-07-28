from __future__ import annotations

import time
from collections import defaultdict


class RateLimiter:
    def __init__(self, max_requests: int = 100, window_seconds: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> bool:
        now = time.monotonic()
        window_start = now - self.window_seconds
        timestamps = self._buckets[key]
        timestamps[:] = [t for t in timestamps if t > window_start]
        if len(timestamps) >= self.max_requests:
            return False
        timestamps.append(now)
        return True

    def remaining(self, key: str) -> int:
        now = time.monotonic()
        window_start = now - self.window_seconds
        timestamps = self._buckets[key]
        timestamps[:] = [t for t in timestamps if t > window_start]
        return max(0, self.max_requests - len(timestamps))

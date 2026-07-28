from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

from traderos.domain.exceptions import ServiceError

T = TypeVar("T")


def retry_with_backoff(
    fn: Callable[..., T],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: bool = True,
) -> T:
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except (ValueError, RuntimeError, OSError, TimeoutError, ServiceError) as e:
            last_exc = e
            if attempt < max_retries:
                delay = min(base_delay * (2**attempt), max_delay)
                if jitter:
                    delay *= 0.5 + random.random() * 0.5
                time.sleep(delay)
    msg = f"Operation failed after {max_retries + 1} attempts"
    raise ServiceError(msg) from last_exc

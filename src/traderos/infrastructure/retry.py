from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

from traderos.domain.exceptions import ServiceError

T = TypeVar("T")

# Exceptions treated as transient dependency failures by default (retried).
_BASE_RETRYABLE = (ValueError, RuntimeError, OSError, TimeoutError, ServiceError)


def retry_with_backoff(
    fn: Callable[..., T],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: bool = True,
    should_retry: Callable[[Exception], bool] | None = None,
) -> T:
    """Retry ``fn`` with exponential backoff on transient failures.

    ``_BASE_RETRYABLE`` exceptions are always retried. ``should_retry`` lets a
    caller additionally classify a NON-base exception (e.g. a broker HTTP error
    that is a bare ``Exception`` subclass) as transient so it is retried too;
    when it returns False the exception propagates immediately (fail fast — a
    permanent broker rejection must not burn retries behind a backoff).
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except _BASE_RETRYABLE as e:
            last_exc = e
        except Exception as e:
            if should_retry is None or not should_retry(e):
                raise
            last_exc = e
        if attempt < max_retries:
            delay = min(base_delay * (2**attempt), max_delay)
            if jitter:
                delay *= 0.5 + random.random() * 0.5
            time.sleep(delay)
    msg = f"Operation failed after {max_retries + 1} attempts"
    raise ServiceError(msg) from last_exc

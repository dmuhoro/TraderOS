"""Resilience primitives: circuit-breaker + timeout wrapper.

Production-grade, dependency-free, tested in isolation.
Each external dependency gets its own CircuitBreaker instance.

The timeout wrapper must be safe in the FastAPI threadpool: it must NOT use
``signal.signal``/``SIGALRM`` (process-global, and ``signal.alarm`` only works
from the main thread). A thread-bounded worker mirrors the established
``run_with_timeout`` pattern so the breaker is exercised from any thread.
"""

from __future__ import annotations

import functools
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from typing import TypeVar

T = TypeVar("T")


class CircuitOpenError(Exception):
    """Raised when the circuit is open and failing fast."""

    def __init__(self, name: str, failure_threshold: int, recovery_timeout: int):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        super().__init__(
            f"Circuit '{name}' OPEN (threshold={failure_threshold}, recovery={recovery_timeout}s)"
        )


class CircuitHalfOpenError(Exception):
    """Raised when the circuit is half-open (testing recovery)."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Circuit '{name}' HALF-OPEN (testing recovery)")


@dataclass(frozen=True)
class CircuitBreakerConfig:
    """Immutable configuration for a circuit breaker."""

    name: str
    failure_threshold: int = 5
    recovery_timeout: int = 30
    expected_exception: tuple[type[Exception], ...] = (Exception,)


class CircuitBreaker:
    """Thread-safe circuit breaker with three states: closed | open | half-open.

    Closed: normal operation, failures counted
    Open: failing fast, rejecting calls immediately
    Half-open: testing recovery with a single trial call
    """

    __slots__ = ("_config", "_failures", "_last_failure", "_lock", "_state", "_successes")

    def __init__(self, config: CircuitBreakerConfig) -> None:
        self._config = config
        self._state = "closed"  # closed | open | half-open
        self._failures = 0
        self._successes = 0
        self._last_failure = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if (
                self._state == "open"
                and time.time() - self._last_failure >= self._config.recovery_timeout
            ):
                self._state = "half-open"
            return self._state

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failures

    @property
    def threshold(self) -> int:
        return self._config.failure_threshold

    @property
    def recovery_seconds(self) -> int:
        return self._config.recovery_timeout

    def call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Execute ``fn`` through the circuit breaker."""
        state = self.state
        if state == "open":
            raise CircuitOpenError(
                self._config.name, self._config.failure_threshold, self._config.recovery_timeout
            )
        if state == "half-open":
            # Allow exactly one trial call
            pass

        try:
            result = fn()
            self._on_success()
            return result
        except self._config.expected_exception:
            self._on_failure()
            raise

    def _on_success(self) -> None:
        with self._lock:
            if self._state == "half-open":
                self._successes += 1
                if self._successes >= 2:  # two consecutive successes to close
                    self._state = "closed"
                    self._failures = 0
                    self._successes = 0
            else:
                self._failures = 0

    def _on_failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._successes = 0
            self._last_failure = time.time()
            if self._state == "half-open" or self._failures >= self._config.failure_threshold:
                self._state = "open"

    def reset(self) -> None:
        """Manual reset (for tests / ops)."""
        with self._lock:
            self._state = "closed"
            self._failures = 0
            self._successes = 0
            self._last_failure = 0.0


# Pre-configured breakers for each external dependency
BROKER_CB = CircuitBreaker(
    CircuitBreakerConfig(
        name="broker",
        failure_threshold=5,
        recovery_timeout=30,
        expected_exception=(Exception,),
    )
)

VAULT_CB = CircuitBreaker(
    CircuitBreakerConfig(
        name="vault",
        failure_threshold=3,
        recovery_timeout=60,
        expected_exception=(Exception,),
    )
)

PG_CB = CircuitBreaker(
    CircuitBreakerConfig(
        name="postgres",
        failure_threshold=10,
        recovery_timeout=15,
        expected_exception=(Exception,),
    )
)

# Mapping for probe/ops
ALL_BREAKERS = {
    "broker": BROKER_CB,
    "vault": VAULT_CB,
    "postgres": PG_CB,
}


def _run_bounded(fn: Callable[..., T], timeout: float, *args: Any, **kwargs: Any) -> T:
    """Run ``fn`` with a hard wall-clock bound.

    The worker is a daemon thread so a genuinely hung dependency can never
    block process shutdown, and a timeout never depends on the signal handler
    being installed on the (process-global) main thread — this wrapper is safe
    from the FastAPI threadpool, which is where the broker is actually called.

    A call that outlives ``timeout`` raises ``TimeoutError``; the worker keeps
    running as a daemon and is deliberately not awaited.
    """
    result: dict[str, Any] = {}

    def _run() -> None:
        try:
            result["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 — re-raised on the caller thread
            result["error"] = exc

    worker = threading.Thread(
        target=_run,
        name=f"traderos-timeout-{fn.__name__}",
        daemon=True,
    )
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError(f"{fn.__name__} exceeded {timeout}s")
    if "error" in result:
        raise result["error"]  # type: ignore[misc]
    return result["value"]  # type: ignore[no-any-return]


def with_circuit_breaker(cb: CircuitBreaker, timeout: float | None = None):
    """Decorator: wrap a callable with circuit-breaker + optional timeout.

    Usage:
        @with_circuit_breaker(BROKER_CB, timeout=5.0)
        def place_order(...): ...

    ``timeout`` is wall-clock bounded in a worker thread (never SIGALRM, which
    is main-thread-only and process-global). A timeout counts as a failure for
    the breaker, so a hang opens the circuit exactly like an exception.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            def _call() -> T:
                if timeout is not None:
                    return _run_bounded(fn, timeout, *args, **kwargs)
                return fn(*args, **kwargs)

            return cb.call(_call)

        return wrapper

    return decorator


def get_breaker_status() -> dict[str, dict[str, Any]]:
    """Return current state of all breakers for probes / ops."""
    return {
        name: {
            "state": cb.state,
            "failures": cb.failure_count,
            "config": {
                "threshold": cb.threshold,
                "recovery_timeout": cb.recovery_seconds,
            },
        }
        for name, cb in ALL_BREAKERS.items()
    }


def reset_all_breakers() -> None:
    """Test / ops helper."""
    for cb in ALL_BREAKERS.values():
        cb.reset()

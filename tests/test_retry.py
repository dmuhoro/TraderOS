from __future__ import annotations

import time

import pytest

from traderos.domain.exceptions import ServiceError
from traderos.infrastructure.retry import retry_with_backoff


class TestRetryWithBackoff:
    def test_returns_first_success(self) -> None:
        assert retry_with_backoff(lambda: "ok", max_retries=2, base_delay=0) == "ok"

    def test_retries_until_success(self) -> None:
        attempts = {"n": 0}

        def flaky() -> str:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise RuntimeError("transient")
            return "recovered"

        assert retry_with_backoff(flaky, max_retries=3, base_delay=0) == "recovered"
        assert attempts["n"] == 3

    def test_exhausts_retries_then_raises_service_error(self) -> None:
        def always_fails() -> None:
            raise ValueError("permanent")

        with pytest.raises(ServiceError) as exc_info:
            retry_with_backoff(always_fails, max_retries=2, base_delay=0)
        assert "attempts" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, ValueError)
        assert exc_info.value.__cause__.args == ("permanent",)

    def test_max_delay_caps_backoff(self) -> None:
        def always_fails() -> None:
            raise OSError("down")

        start = time.perf_counter()
        with pytest.raises(ServiceError):
            retry_with_backoff(always_fails, max_retries=4, base_delay=1e9, max_delay=1e-6)
        elapsed = time.perf_counter() - start
        # Without the max_delay cap the sleeps would total ~2e9 seconds.
        assert elapsed < 1.0

    def test_unlisted_exception_propagates_without_retry(self) -> None:
        # A programming error (KeyError) is not a transient dependency failure:
        # it must surface immediately, not burn retries behind a backoff.
        calls = {"n": 0}

        def key_error() -> None:
            calls["n"] += 1
            raise KeyError("bug")

        with pytest.raises(KeyError):
            retry_with_backoff(key_error, max_retries=3, base_delay=0)
        assert calls["n"] == 1

    def test_should_retry_predicate_retries_matching_exception(self) -> None:
        # A bare Exception subclass (e.g. a broker APIError with status_code)
        # that is NOT in the base retryable set can be retried via a predicate.
        class BrokerHTTPError(Exception):
            def __init__(self, status_code: int):
                super().__init__(f"http {status_code}")
                self.status_code = status_code

        attempts = {"n": 0}

        def flaky() -> str:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise BrokerHTTPError(503)
            return "recovered"

        assert (
            retry_with_backoff(
                flaky,
                max_retries=3,
                base_delay=0,
                should_retry=lambda e: isinstance(e, BrokerHTTPError)
                and getattr(e, "status_code", None) in (429, 500, 502, 503, 504),
            )
            == "recovered"
        )
        assert attempts["n"] == 3

    def test_should_retry_predicate_false_propagates_immediately(self) -> None:
        # A permanent 4xx must not burn retries behind a backoff.
        class BrokerHTTPError(Exception):
            def __init__(self, status_code: int):
                super().__init__(f"http {status_code}")
                self.status_code = status_code

        calls = {"n": 0}

        def permanent() -> None:
            calls["n"] += 1
            raise BrokerHTTPError(400)

        with pytest.raises(BrokerHTTPError):
            retry_with_backoff(
                permanent,
                max_retries=3,
                base_delay=0,
                should_retry=lambda e: isinstance(e, BrokerHTTPError)
                and getattr(e, "status_code", None) in (429, 500, 502, 503, 504),
            )
        assert calls["n"] == 1

    def test_should_retry_predicate_matching_exhausts_then_service_error(self) -> None:
        class BrokerHTTPError(Exception):
            def __init__(self, status_code: int):
                super().__init__(f"http {status_code}")
                self.status_code = status_code

        def always_503() -> None:
            raise BrokerHTTPError(503)

        with pytest.raises(ServiceError) as exc_info:
            retry_with_backoff(
                always_503,
                max_retries=2,
                base_delay=0,
                should_retry=lambda e: isinstance(e, BrokerHTTPError)
                and getattr(e, "status_code", None) in (429, 500, 502, 503, 504),
            )
        assert isinstance(exc_info.value.__cause__, BrokerHTTPError)

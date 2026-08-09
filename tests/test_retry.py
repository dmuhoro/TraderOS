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

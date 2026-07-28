from __future__ import annotations

from traderos.infrastructure.rate_limiter import RateLimiter


class TestRateLimiter:
    def test_allows_within_limit(self):
        limiter = RateLimiter(max_requests=5, window_seconds=60.0)
        for _ in range(5):
            assert limiter.check("test-key") is True

    def test_blocks_over_limit(self):
        limiter = RateLimiter(max_requests=3, window_seconds=60.0)
        for _ in range(3):
            limiter.check("test-key")
        assert limiter.check("test-key") is False

    def test_remaining_count(self):
        limiter = RateLimiter(max_requests=10, window_seconds=60.0)
        assert limiter.remaining("test-key") == 10
        limiter.check("test-key")
        assert limiter.remaining("test-key") == 9

    def test_different_keys_independent(self):
        limiter = RateLimiter(max_requests=2, window_seconds=60.0)
        assert limiter.check("key-a") is True
        assert limiter.check("key-a") is True
        assert limiter.check("key-a") is False
        assert limiter.check("key-b") is True

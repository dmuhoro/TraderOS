from __future__ import annotations

import uuid

import pytest

from traderos.domain.adapters.broker_adapter import FillResult
from traderos.infrastructure.broker_rate_limiter import RateLimitedBroker
from traderos.infrastructure.broker_rate_limiter import RateLimitExceededError


class _MockInner:
    def __init__(self) -> None:
        self.call_count = 0

    def place_market_order(self, market_id, side, quantity, close_price=None):
        self.call_count += 1
        return FillResult(True, quantity, 100.0, 0.0, "filled", "ord1")

    def place_limit_order(self, market_id, side, quantity, price, close_price=None):
        self.call_count += 1
        return FillResult(False, 0.0, 0.0, quantity, "pending", "")

    def cancel_order(self, order_id):
        self.call_count += 1
        return FillResult(True, 0.0, 0.0, 0.0, "cancelled", order_id)

    def get_account_balance(self):
        self.call_count += 1
        return 10000.0

    def get_positions(self):
        self.call_count += 1
        return []

    def get_open_orders(self):
        self.call_count += 1
        return []


class TestRateLimitedBroker:
    def test_disabled_by_default_passes_all(self) -> None:
        inner = _MockInner()
        broker = RateLimitedBroker(inner, max_requests=1, window_seconds=60.0)
        for _ in range(10):
            broker.place_market_order(uuid.uuid4(), "buy", 1.0)
        assert inner.call_count == 10

    def test_enabled_blocks_when_over_limit(self, monkeypatch) -> None:
        monkeypatch.setenv("BROKER_RATE_LIMIT_ENABLED", "true")
        inner = _MockInner()
        broker = RateLimitedBroker(inner, max_requests=2, window_seconds=60.0)

        mid = uuid.uuid4()
        broker.place_market_order(mid, "buy", 1.0)
        broker.place_market_order(mid, "buy", 1.0)
        with pytest.raises(RateLimitExceededError):
            broker.place_market_order(mid, "buy", 1.0)
        assert inner.call_count == 2

    def test_different_methods_have_separate_buckets(self, monkeypatch) -> None:
        monkeypatch.setenv("BROKER_RATE_LIMIT_ENABLED", "true")
        inner = _MockInner()
        broker = RateLimitedBroker(inner, max_requests=1, window_seconds=60.0)

        mid = uuid.uuid4()
        broker.place_market_order(mid, "buy", 1.0)
        broker.get_account_balance()
        broker.get_positions()
        assert inner.call_count == 3

        with pytest.raises(RateLimitExceededError):
            broker.place_market_order(mid, "buy", 1.0)

    def test_env_var_config_loaded(self, monkeypatch) -> None:
        monkeypatch.setenv("BROKER_RATE_LIMIT_ENABLED", "true")
        monkeypatch.setenv("BROKER_RATE_LIMIT_MAX", "5")
        monkeypatch.setenv("BROKER_RATE_LIMIT_WINDOW", "10")
        inner = _MockInner()
        broker = RateLimitedBroker(inner)

        mid = uuid.uuid4()
        for _ in range(5):
            broker.place_market_order(mid, "buy", 1.0)
        assert inner.call_count == 5

        with pytest.raises(RateLimitExceededError):
            broker.place_market_order(mid, "buy", 1.0)
        assert inner.call_count == 5

    def test_all_broker_methods_wrapped(self, monkeypatch) -> None:
        monkeypatch.setenv("BROKER_RATE_LIMIT_ENABLED", "true")
        inner = _MockInner()
        broker = RateLimitedBroker(inner, max_requests=0, window_seconds=60.0)

        mid = uuid.uuid4()
        with pytest.raises(RateLimitExceededError):
            broker.place_market_order(mid, "buy", 1.0)
        with pytest.raises(RateLimitExceededError):
            broker.place_limit_order(mid, "buy", 1.0, 100.0)
        with pytest.raises(RateLimitExceededError):
            broker.cancel_order("ord1")
        with pytest.raises(RateLimitExceededError):
            broker.get_account_balance()
        with pytest.raises(RateLimitExceededError):
            broker.get_positions()
        with pytest.raises(RateLimitExceededError):
            broker.get_open_orders()
        assert inner.call_count == 0

    def test_disabled_even_with_env_set_to_false(self, monkeypatch) -> None:
        monkeypatch.setenv("BROKER_RATE_LIMIT_ENABLED", "false")
        inner = _MockInner()
        broker = RateLimitedBroker(inner, max_requests=1, window_seconds=60.0)

        for _ in range(5):
            broker.place_market_order(uuid.uuid4(), "buy", 1.0)
        assert inner.call_count == 5

from __future__ import annotations

import uuid

from traderos.domain.adapters.broker_adapter import FillResult
from traderos.infrastructure.order_guardrail import GuardrailedBroker


class _MockInner:
    def __init__(self) -> None:
        self.place_calls = 0

    def place_market_order(self, market_id, side, quantity, close_price=None, client_order_id=None):
        self.place_calls += 1
        return FillResult(True, quantity, 100.0, 0.0, "filled", "ord1")

    def place_flatten_order(self, market_id, side, quantity, close_price=None):
        self.place_calls += 1
        return FillResult(True, quantity, close_price or 100.0, 0.0, "filled", "ord1")

    def place_limit_order(self, market_id, side, quantity, price, close_price=None):
        self.place_calls += 1
        return FillResult(False, 0.0, 0.0, quantity, "pending", "")

    def cancel_order(self, order_id):
        return FillResult(True, 0.0, 0.0, 0.0, "cancelled", order_id)

    def place_stop_order(self, market_id, side, quantity, stop_price, market_price=None):
        self.place_calls += 1
        return FillResult(False, 0.0, 0.0, quantity, "pending", "")

    def place_trailing_stop_order(
        self, market_id, side, quantity, trail_percent, market_price=None
    ):
        self.place_calls += 1
        return FillResult(False, 0.0, 0.0, quantity, "pending", "")

    def modify_order(
        self, order_id, qty=None, limit_price=None, stop_price=None, trail_percent=None
    ):
        self.place_calls += 1
        return FillResult(True, 0.0, 0.0, 0.0, "modified", order_id)

    def get_account_balance(self):
        return 10000.0

    def get_positions(self):
        return []

    def get_open_orders(self):
        return []


class TestGuardrailedBroker:
    def test_min_qty_rejected_before_inner(self) -> None:
        inner = _MockInner()
        broker = GuardrailedBroker(inner, min_order_qty=1.0, max_order_notional=500.0)
        result = broker.place_market_order(uuid.uuid4(), "buy", 0.5, close_price=100.0)
        assert result.filled is False
        assert result.status == "rejected"
        assert "below minimum" in result.order_id
        assert inner.place_calls == 0

    def test_max_notional_rejected(self) -> None:
        inner = _MockInner()
        broker = GuardrailedBroker(inner, min_order_qty=1.0, max_order_notional=500.0)
        result = broker.place_market_order(uuid.uuid4(), "buy", 10.0, close_price=100.0)
        assert result.status == "rejected"
        assert "exceeds maximum" in result.order_id
        assert inner.place_calls == 0

    def test_flatten_bypasses_guardrail_even_when_oversized(self) -> None:
        inner = _MockInner()
        broker = GuardrailedBroker(inner, min_order_qty=1.0, max_order_notional=500.0)
        # Same shape that place_market_order rejects above must NOT block the
        # emergency close: the kill switch is never refused by size policy.
        result = broker.place_flatten_order(uuid.uuid4(), "sell", 10.0, close_price=100.0)
        assert result.filled is True
        assert inner.place_calls == 1

    def test_flatten_bypasses_min_qty_guard(self) -> None:
        inner = _MockInner()
        broker = GuardrailedBroker(inner, min_order_qty=1.0, max_order_notional=500.0)
        result = broker.place_flatten_order(uuid.uuid4(), "sell", 0.1, close_price=100.0)
        assert result.filled is True
        assert inner.place_calls == 1

    def test_valid_order_passes_through(self) -> None:
        inner = _MockInner()
        broker = GuardrailedBroker(inner, min_order_qty=1.0, max_order_notional=500.0)
        result = broker.place_market_order(uuid.uuid4(), "buy", 2.0, close_price=100.0)
        assert result.filled is True
        assert inner.place_calls == 1

    def test_no_price_skips_notional_check(self) -> None:
        inner = _MockInner()
        broker = GuardrailedBroker(inner, min_order_qty=1.0, max_order_notional=500.0)
        result = broker.place_market_order(uuid.uuid4(), "buy", 10.0, close_price=None)
        assert result.filled is True
        assert inner.place_calls == 1

    def test_limit_stop_trailing_guarded(self) -> None:
        inner = _MockInner()
        broker = GuardrailedBroker(inner, min_order_qty=1.0, max_order_notional=500.0)
        mid = uuid.uuid4()
        assert broker.place_limit_order(mid, "buy", 10.0, 100.0).status == "rejected"
        assert broker.place_stop_order(mid, "buy", 10.0, 100.0).status == "rejected"
        assert broker.place_trailing_stop_order(mid, "buy", 10.0, 0.01, 100.0).status == "rejected"
        assert inner.place_calls == 0

    def test_modify_qty_guarded_but_price_only_passes(self) -> None:
        inner = _MockInner()
        broker = GuardrailedBroker(inner, min_order_qty=1.0, max_order_notional=500.0)
        assert broker.modify_order("ord1", qty=0.5).status == "rejected"
        assert inner.place_calls == 0
        result = broker.modify_order("ord1", limit_price=101.0)
        assert result.status == "modified"
        assert inner.place_calls == 1

    def test_reads_and_cancel_pass_through(self) -> None:
        inner = _MockInner()
        broker = GuardrailedBroker(inner, min_order_qty=1.0, max_order_notional=500.0)
        assert broker.cancel_order("ord1").status == "cancelled"
        assert broker.get_account_balance() == 10000.0
        assert broker.get_positions() == []
        assert broker.get_open_orders() == []

    def test_disabled_passes_through(self) -> None:
        inner = _MockInner()
        broker = GuardrailedBroker(
            inner, min_order_qty=1.0, max_order_notional=500.0, enabled=False
        )
        result = broker.place_market_order(uuid.uuid4(), "buy", 0.1, close_price=100.0)
        assert result.filled is True
        assert inner.place_calls == 1

    def test_env_var_config_loaded(self, monkeypatch) -> None:
        monkeypatch.setenv("TRADEROS_MAX_ORDER_NOTIONAL", "100")
        inner = _MockInner()
        broker = GuardrailedBroker(inner, min_order_qty=1.0)
        result = broker.place_market_order(uuid.uuid4(), "buy", 2.0, close_price=100.0)
        assert result.status == "rejected"
        assert inner.place_calls == 0

    def test_env_bool_accepts_explicit_values(self, monkeypatch) -> None:
        monkeypatch.setenv("TRADEROS_ORDER_GUARDRAIL_ENABLED", "true")
        inner = _MockInner()
        broker = GuardrailedBroker(inner, min_order_qty=1.0)
        result = broker.place_market_order(uuid.uuid4(), "buy", 0.5, close_price=100.0)
        assert result.status == "rejected"  # still guarded when env explicitly "true"

    def test_stop_and_trailing_pass_through_when_in_bounds(self) -> None:
        inner = _MockInner()
        broker = GuardrailedBroker(inner, min_order_qty=1.0, max_order_notional=500.0)
        mid = uuid.uuid4()
        assert broker.place_stop_order(mid, "buy", 2.0, 90.0).status == "pending"
        assert broker.place_trailing_stop_order(mid, "buy", 2.0, 0.01, 100.0).status == "pending"
        assert inner.place_calls == 2

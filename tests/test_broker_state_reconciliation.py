from __future__ import annotations

from traderos.domain.ports import BrokerPort
from traderos.domain.services.broker_state_reconciliation_service import (
    BrokerStateReconciliationService,
)


class _ReconcilableBroker:
    def place_market_order(self, market_id, side, quantity, close_price=None):
        return None

    def place_limit_order(self, market_id, side, quantity, price, close_price=None):
        return None

    def cancel_order(self, order_id):
        return None

    def get_account_balance(self):
        return 10000.0

    def get_positions(self):
        return [{"symbol": "BTC/USD", "qty": 1.0, "market_value": 50000.0}]

    def get_open_orders(self):
        return [{"id": "ord-1", "symbol": "BTC/USD", "qty": 0.5, "side": "buy", "type": "limit"}]


class _FailingBroker:
    def place_market_order(self, market_id, side, quantity, close_price=None):
        return None

    def place_limit_order(self, market_id, side, quantity, price, close_price=None):
        return None

    def cancel_order(self, order_id):
        return None

    def get_account_balance(self):
        raise RuntimeError("Broker unreachable")

    def get_positions(self):
        raise RuntimeError("Broker unreachable")

    def get_open_orders(self):
        raise RuntimeError("Broker unreachable")


class TestBrokerStateReconciliationService:
    def test_startup_not_reconciled_initially(self) -> None:
        svc = BrokerStateReconciliationService(broker=_ReconcilableBroker())
        assert not svc.startup_reconciled
        assert not svc.can_accept_orders

    def test_reconcile_success_sets_startup_flag(self) -> None:
        svc = BrokerStateReconciliationService(broker=_ReconcilableBroker())
        result = svc.reconcile()
        assert svc.startup_reconciled
        assert svc.can_accept_orders
        assert result.matched_positions == 1
        assert result.reconciled_positions == 2
        assert not result.errors
        assert svc.consecutive_failures == 0

    def test_reconcile_failure_keeps_startup_blocked(self) -> None:
        svc = BrokerStateReconciliationService(broker=_FailingBroker())
        result = svc.reconcile()
        assert not svc.startup_reconciled
        assert not svc.can_accept_orders
        assert len(result.errors) == 1
        assert "Failed to fetch broker state" in result.errors[0]
        assert svc.consecutive_failures == 1

    def test_reconcile_recovers_after_failure(self) -> None:
        svc = BrokerStateReconciliationService(broker=_FailingBroker())
        svc.reconcile()
        assert not svc.startup_reconciled
        assert svc.consecutive_failures == 1

        svc = BrokerStateReconciliationService(broker=_ReconcilableBroker())
        result = svc.reconcile()
        assert svc.startup_reconciled
        assert result.matched_positions == 1
        assert svc.consecutive_failures == 0

    def test_empty_broker_reconciles(self) -> None:
        class _EmptyBroker:
            def place_market_order(self, market_id, side, quantity, close_price=None):
                return None
            def place_limit_order(self, market_id, side, quantity, price, close_price=None):
                return None
            def cancel_order(self, order_id):
                return None
            def get_account_balance(self):
                return 0.0
            def get_positions(self):
                return []
            def get_open_orders(self):
                return []

        svc = BrokerStateReconciliationService(broker=_EmptyBroker())
        result = svc.reconcile()
        assert svc.startup_reconciled
        assert result.matched_positions == 0
        assert not result.errors

from __future__ import annotations

from traderos.domain.services.broker_state_reconciliation_service import (
    BrokerStateReconciliationService,
)
from traderos.domain.services.broker_state_reconciliation_service import MismatchType


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
        return [{"symbol": "BTC/USD", "qty": 1.0, "current_price": 50000.0}]

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
        local_positions = [{"symbol": "BTC/USD", "qty": 1.0, "current_price": 50000.0}]
        local_orders = [
            {"id": "ord-1", "symbol": "BTC/USD", "qty": 0.5, "side": "buy", "type": "limit"}
        ]
        result = svc.reconcile(local_positions=local_positions, local_orders=local_orders)
        assert svc.startup_reconciled
        assert svc.can_accept_orders
        assert result.matched_positions == 1
        assert not result.errors
        assert not result.has_mismatches
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
        svc_fail = BrokerStateReconciliationService(broker=_FailingBroker())
        svc_fail.reconcile()
        assert not svc_fail.startup_reconciled
        assert svc_fail.consecutive_failures == 1

        svc = BrokerStateReconciliationService(broker=_ReconcilableBroker())
        local_positions = [{"symbol": "BTC/USD", "qty": 1.0, "current_price": 50000.0}]
        local_orders = [
            {"id": "ord-1", "symbol": "BTC/USD", "qty": 0.5, "side": "buy", "type": "limit"}
        ]
        result = svc.reconcile(local_positions=local_positions, local_orders=local_orders)
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

    def test_broker_only_position_detected(self) -> None:
        svc = BrokerStateReconciliationService(broker=_ReconcilableBroker())
        result = svc.reconcile(local_positions=[], local_orders=[])
        assert result.has_mismatches
        assert any(m.mismatch_type == MismatchType.BROKER_ONLY_POSITION for m in result.mismatches)
        assert svc.consecutive_failures == 1

    def test_local_only_position_detected(self) -> None:
        class _EmptyPosBroker:
            def get_positions(self):
                return []

            def get_open_orders(self):
                return []

            def get_account_balance(self):
                return 0.0

            def place_market_order(self, *a, **kw):
                return None

            def place_limit_order(self, *a, **kw):
                return None

            def cancel_order(self, oid):
                return None

        svc = BrokerStateReconciliationService(broker=_EmptyPosBroker())
        result = svc.reconcile(
            local_positions=[{"symbol": "ETH/USD", "qty": 2.0, "current_price": 3000.0}],
            local_orders=[],
        )
        assert result.has_mismatches
        assert any(m.mismatch_type == MismatchType.LOCAL_ONLY_POSITION for m in result.mismatches)

    def test_quantity_mismatch_detected(self) -> None:
        svc = BrokerStateReconciliationService(broker=_ReconcilableBroker())
        result = svc.reconcile(
            local_positions=[{"symbol": "BTC/USD", "qty": 2.0, "current_price": 50000.0}],
            local_orders=[{"id": "ord-1"}],
        )
        assert result.has_mismatches
        assert any(m.mismatch_type == MismatchType.QUANTITY_MISMATCH for m in result.mismatches)

    def test_price_mismatch_detected(self) -> None:
        svc = BrokerStateReconciliationService(broker=_ReconcilableBroker())
        result = svc.reconcile(
            local_positions=[{"symbol": "BTC/USD", "qty": 1.0, "current_price": 60000.0}],
            local_orders=[{"id": "ord-1"}],
        )
        assert result.has_mismatches
        assert any(m.mismatch_type == MismatchType.PRICE_MISMATCH for m in result.mismatches)

    def test_broker_only_order_detected(self) -> None:
        svc = BrokerStateReconciliationService(broker=_ReconcilableBroker())
        result = svc.reconcile(
            local_positions=[{"symbol": "BTC/USD", "qty": 1.0, "current_price": 50000.0}],
            local_orders=[],
        )
        assert result.has_mismatches
        assert any(m.mismatch_type == MismatchType.BROKER_ONLY_ORDER for m in result.mismatches)

    def test_local_only_order_detected(self) -> None:
        class _EmptyOrdBroker:
            def get_positions(self):
                return []

            def get_open_orders(self):
                return []

            def get_account_balance(self):
                return 0.0

            def place_market_order(self, *a, **kw):
                return None

            def place_limit_order(self, *a, **kw):
                return None

            def cancel_order(self, oid):
                return None

        svc = BrokerStateReconciliationService(broker=_EmptyOrdBroker())
        result = svc.reconcile(
            local_positions=[],
            local_orders=[{"id": "local-ord-1"}],
        )
        assert result.has_mismatches
        assert any(m.mismatch_type == MismatchType.LOCAL_ONLY_ORDER for m in result.mismatches)

    def test_duplicate_broker_state_detected(self) -> None:
        class _DupBroker:
            def get_positions(self):
                return [
                    {"symbol": "BTC/USD", "qty": 1.0, "current_price": 50000.0},
                    {"symbol": "BTC/USD", "qty": 0.5, "current_price": 51000.0},
                ]

            def get_open_orders(self):
                return []

            def get_account_balance(self):
                return 0.0

            def place_market_order(self, *a, **kw):
                return None

            def place_limit_order(self, *a, **kw):
                return None

            def cancel_order(self, oid):
                return None

        svc = BrokerStateReconciliationService(broker=_DupBroker())
        result = svc.reconcile(local_positions=[], local_orders=[])
        dupes = [
            m for m in result.mismatches if m.mismatch_type == MismatchType.DUPLICATE_BROKER_STATE
        ]
        assert len(dupes) >= 1

    def test_reconciliation_fails_closed_on_broker_failure(self) -> None:
        svc = BrokerStateReconciliationService(broker=_FailingBroker())
        result = svc.reconcile()
        assert result.failed
        assert result.errors[0].startswith("Failed to fetch broker state")
        assert not svc.can_accept_orders

    def test_reconcile_with_all_10_mismatches_integration(self) -> None:
        class _MultiIssueBroker:
            def get_positions(self):
                return [
                    {"symbol": "BTC/USD", "qty": 1.0, "current_price": 50000.0},
                    {"symbol": "DUP", "qty": 2.0, "current_price": 100.0},
                    {"symbol": "DUP", "qty": 3.0, "current_price": 101.0},
                ]

            def get_open_orders(self):
                return [
                    {"id": "broker-ord-1", "symbol": "BTC/USD"},
                    {"id": "broker-ord-2", "symbol": "ETH/USD"},
                ]

            def get_account_balance(self):
                return 0.0

            def place_market_order(self, *a, **kw):
                return None

            def place_limit_order(self, *a, **kw):
                return None

            def cancel_order(self, oid):
                return None

        svc = BrokerStateReconciliationService(broker=_MultiIssueBroker())
        local_positions = [
            {"symbol": "BTC/USD", "qty": 1.0, "current_price": 50000.0},
            {"symbol": "LOCAL_ONLY", "qty": 5.0, "current_price": 50.0},
        ]
        local_orders = [
            {"id": "broker-ord-1", "symbol": "BTC/USD"},
            {"id": "local-only-ord", "symbol": "BTC/USD"},
        ]
        result = svc.reconcile(local_positions=local_positions, local_orders=local_orders)

        types_found = {m.mismatch_type for m in result.mismatches}
        assert MismatchType.LOCAL_ONLY_POSITION in types_found
        assert MismatchType.BROKER_ONLY_POSITION in types_found
        assert MismatchType.DUPLICATE_BROKER_STATE in types_found
        assert MismatchType.BROKER_ONLY_ORDER in types_found
        assert MismatchType.LOCAL_ONLY_ORDER in types_found
        assert result.failed

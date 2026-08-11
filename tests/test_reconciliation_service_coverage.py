from __future__ import annotations

import uuid
from types import SimpleNamespace

from traderos.domain.entities.trade import Trade
from traderos.domain.entities.trade import TradeSide
from traderos.domain.entities.trade import TradeStatus
from traderos.domain.services.reconciliation_service import KillSwitchState
from traderos.domain.services.reconciliation_service import OrderReconciliationService
from traderos.domain.services.reconciliation_service import OrderState
from traderos.domain.services.reconciliation_service import PersistentKillSwitch
from traderos.domain.services.reconciliation_service import PositionState


def _trade(status: TradeStatus = TradeStatus.SUBMITTED, ext_id: str | None = "o1") -> Trade:
    return Trade(
        signal_id=uuid.uuid4(),
        market_id=uuid.uuid4(),
        side=TradeSide.BUY,
        quantity=1.0,
        price=100.0,
        status=status,
        external_order_id=ext_id,
    )


class TestOrderReconciliation:
    def test_local_trade_without_broker_order_is_cancelled(self) -> None:
        svc = OrderReconciliationService()
        trade = _trade()
        result = svc.reconcile_orders([trade], [])
        assert result.orphaned_local == 1
        assert result.reconciled == 1
        assert trade.status == TradeStatus.CANCELLED

    def test_matched_submitted_trade_is_filled_from_broker(self) -> None:
        svc = OrderReconciliationService()
        trade = _trade()
        broker_orders = [
            OrderState(
                order_id="o1",
                status="filled",
                filled_qty=1.0,
                filled_price=100.5,
                remaining_qty=0.0,
                symbol="BTCUSDT",
            )
        ]
        result = svc.reconcile_orders([trade], broker_orders)
        assert result.matched == 1
        assert result.reconciled == 1
        assert trade.status == TradeStatus.FILLED
        assert trade.filled_quantity == 1.0
        assert trade.filled_price == 100.5

    def test_broker_order_without_local_trade_is_orphaned(self) -> None:
        svc = OrderReconciliationService()
        broker_orders = [
            OrderState(
                order_id="o2",
                status="open",
                filled_qty=0.0,
                filled_price=0.0,
                remaining_qty=1.0,
                symbol="BTCUSDT",
            )
        ]
        result = svc.reconcile_orders([], broker_orders)
        assert result.orphaned_broker == 1

    def test_partial_fill_mismatch_is_reconciled(self) -> None:
        svc = OrderReconciliationService()
        trade = _trade(status=TradeStatus.PARTIALLY_FILLED)
        broker_orders = [
            OrderState(
                order_id="o1",
                status="partially_filled",
                filled_qty=0.5,
                filled_price=100.0,
                remaining_qty=0.5,
                symbol="BTCUSDT",
            )
        ]
        result = svc.reconcile_orders([trade], broker_orders)
        assert result.reconciled == 1
        assert trade.filled_quantity == 0.5


class TestPositionReconciliation:
    def _local(self, qty: float) -> SimpleNamespace:
        return SimpleNamespace(
            symbol="BTCUSDT", quantity=qty, market_value=1000.0, entry_price=100.0
        )

    def test_matching_positions_no_errors(self) -> None:
        svc = OrderReconciliationService()
        lp = [self._local(1.0)]
        bp = [PositionState(symbol="BTCUSDT", quantity=1.0, market_value=1000.0, entry_price=100.0)]
        result = svc.reconcile_positions(lp, bp)
        assert result.matched == 1
        assert result.reconciled == 0
        assert result.errors == []

    def test_quantity_mismatch_is_reconciled(self) -> None:
        svc = OrderReconciliationService()
        lp = [self._local(1.0)]
        bp = [PositionState(symbol="BTCUSDT", quantity=0.5, market_value=500.0, entry_price=100.0)]
        result = svc.reconcile_positions(lp, bp)
        assert result.reconciled == 1

    def test_count_mismatch_adds_error(self) -> None:
        svc = OrderReconciliationService()
        lp = [self._local(1.0), self._local(2.0)]
        bp = [PositionState(symbol="BTCUSDT", quantity=1.0, market_value=1000.0, entry_price=100.0)]
        result = svc.reconcile_positions(lp, bp)
        assert result.matched == 1
        assert "count mismatch" in result.errors[0]


class TestPersistentKillSwitch:
    def test_restore_state_and_success_reset(self) -> None:
        ks = PersistentKillSwitch()
        restored = KillSwitchState(
            consecutive_failures=3, daily_loss=-500.0, circuit_open=False, last_reset=None
        )
        ks.restore_state(restored)
        assert ks.state.consecutive_failures == 3
        ks.record_success()
        assert ks.state.consecutive_failures == 0

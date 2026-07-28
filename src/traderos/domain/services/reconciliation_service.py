from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from typing import Any

from traderos.domain.entities.trade import Trade
from traderos.domain.entities.trade import TradeStatus
from traderos.domain.exceptions import DomainError


class ReconciliationError(DomainError):
    pass


@dataclass
class ReconciliationResult:
    matched: int = 0
    reconciled: int = 0
    orphaned_local: int = 0
    orphaned_broker: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class OrderState:
    order_id: str
    status: str
    filled_qty: float
    filled_price: float
    remaining_qty: float
    symbol: str


@dataclass
class PositionState:
    symbol: str
    quantity: float
    market_value: float
    entry_price: float


class OrderReconciliationService:
    def reconcile_orders(
        self,
        local_trades: list[Trade],
        broker_orders: list[OrderState],
    ) -> ReconciliationResult:
        result = ReconciliationResult()
        broker_by_id: dict[str, OrderState] = {o.order_id: o for o in broker_orders}
        local_by_ext_id: dict[str, Trade] = {
            t.external_order_id: t for t in local_trades if t.external_order_id
        }

        for ext_id, trade in local_by_ext_id.items():
            broker_order = broker_by_id.get(ext_id)
            if broker_order is None:
                result.orphaned_local += 1
                if trade.status in (TradeStatus.SUBMITTED, TradeStatus.PENDING):
                    trade.cancel()
                    result.reconciled += 1
                continue
            result.matched += 1
            if trade.status == TradeStatus.SUBMITTED and (
                broker_order.filled_qty > 0 or broker_order.status == "filled"
            ):
                trade.fill(broker_order.filled_qty, broker_order.filled_price)
                result.reconciled += 1
            elif trade.status == TradeStatus.PARTIALLY_FILLED and (
                abs(broker_order.filled_qty - trade.filled_quantity) > 0.0001
            ):
                trade.fill(broker_order.filled_qty, broker_order.filled_price)
                result.reconciled += 1

        for ext_id, _broker_order in broker_by_id.items():
            if ext_id not in local_by_ext_id:
                result.orphaned_broker += 1

        return result

    def reconcile_positions(
        self,
        local_positions: list[Any],
        broker_positions: list[PositionState],
    ) -> ReconciliationResult:
        result = ReconciliationResult()
        for lp, bp in zip(local_positions, broker_positions, strict=False):
            if abs(lp.quantity - bp.quantity) > 0.0001:
                result.reconciled += 1
        result.matched = min(len(local_positions), len(broker_positions))
        if len(local_positions) != len(broker_positions):
            result.errors.append(
                "Local/broker position count mismatch: "
                f"{len(local_positions)} vs {len(broker_positions)}"
            )
        return result


@dataclass
class KillSwitchState:
    consecutive_failures: int = 0
    daily_loss: float = 0.0
    circuit_open: bool = False
    last_reset: datetime | None = None


class PersistentKillSwitch:
    def __init__(
        self,
        max_consecutive_failures: int = 5,
        daily_loss_limit: float = float("inf"),
    ) -> None:
        self._max_failures = max_consecutive_failures
        self._daily_loss_limit = daily_loss_limit
        self._state = KillSwitchState()

    @property
    def state(self) -> KillSwitchState:
        return self._state

    def restore_state(self, state: KillSwitchState) -> None:
        self._state = state

    def record_failure(self) -> None:
        self._state.consecutive_failures += 1
        if self._state.consecutive_failures >= self._max_failures:
            self._state.circuit_open = True

    def record_success(self) -> None:
        self._state.consecutive_failures = 0

    def record_realized_pnl(self, pnl: float) -> None:
        self._state.daily_loss += pnl

    def can_trade(self) -> bool:
        if self._state.circuit_open:
            return False
        if self._state.consecutive_failures >= self._max_failures:
            return False
        if abs(self._state.daily_loss) >= self._daily_loss_limit:
            return False
        return True

    def reset(self) -> None:
        self._state = KillSwitchState(last_reset=datetime.now(UTC))

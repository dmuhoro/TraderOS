from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from enum import Enum

from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.adapters.broker_adapter import FillResult
from traderos.domain.entities import Position
from traderos.domain.entities import Trade
from traderos.domain.entities import TradeSide
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.execution_service import Order
from traderos.domain.services.execution_service import OrderStatus
from traderos.domain.services.portfolio_service import PortfolioService
from traderos.domain.services.risk_service import RiskService
from traderos.domain.services.signal_service import SignalService


class PaperSessionStatus(Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


@dataclass
class PaperSession:
    id: uuid.UUID
    strategy_id: uuid.UUID
    market_ids: list[uuid.UUID]
    status: PaperSessionStatus
    start_time: datetime | None = None
    end_time: datetime | None = None
    initial_capital: float = float(os.getenv("DEFAULT_CASH", "10000.0"))
    current_capital: float = float(os.getenv("DEFAULT_CASH", "10000.0"))
    open_orders: list[Order] = field(default_factory=list)
    filled_orders: list[Order] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    positions: dict[uuid.UUID, Position] = field(default_factory=dict)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class PaperBrokerAdapter(BrokerAdapter):
    slippage_bps: float = 5.0
    fill_probability: float = 1.0
    partial_fill_probability: float = 0.0
    latency_ms: int = 0
    account_balance: float = float(os.getenv("DEFAULT_CASH", "10000.0"))

    def _fill_result(
        self,
        filled: bool,
        qty: float,
        price: float,
        remaining: float,
        status: OrderStatus,
    ) -> FillResult:
        return FillResult(filled, qty, price, remaining, status.value, "")

    def place_market_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        close_price: float | None = None,
    ) -> FillResult:
        import random

        if random.random() > self.fill_probability:
            return self._fill_result(False, 0.0, 0.0, quantity, OrderStatus.REJECTED)
        ref_price = close_price if close_price is not None else 1.0
        slip_multiplier = (
            1 + self.slippage_bps / 10000 if side == "buy" else 1 - self.slippage_bps / 10000
        )
        fill_price = ref_price * slip_multiplier
        if random.random() < self.partial_fill_probability:
            fill_qty = quantity * random.uniform(0.1, 0.9)
            remaining = quantity - fill_qty
            return self._fill_result(
                True,
                round(fill_qty, 8),
                round(fill_price, 8),
                round(remaining, 8),
                OrderStatus.PARTIAL,
            )
        return self._fill_result(True, quantity, round(fill_price, 8), 0.0, OrderStatus.FILLED)

    def place_limit_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        price: float,
        close_price: float | None = None,
    ) -> FillResult:
        if close_price is None:
            return self._fill_result(False, 0.0, 0.0, quantity, OrderStatus.PENDING)
        if (side == "buy" and close_price <= price) or (side == "sell" and close_price >= price):
            slip_multiplier = (
                1 + self.slippage_bps / 10000 if side == "buy" else 1 - self.slippage_bps / 10000
            )
            fill_price = close_price * slip_multiplier
            return self._fill_result(True, quantity, round(fill_price, 8), 0.0, OrderStatus.FILLED)
        return self._fill_result(False, 0.0, 0.0, quantity, OrderStatus.PENDING)

    def place_stop_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        stop_price: float,
        market_price: float,
    ) -> FillResult:
        triggered = (side == "buy" and market_price >= stop_price) or (
            side == "sell" and market_price <= stop_price
        )
        if not triggered:
            return self._fill_result(False, 0.0, 0.0, quantity, OrderStatus.PENDING)
        return self.place_market_order(market_id, side, quantity, close_price=market_price)

    def cancel_order(self, order_id: str) -> FillResult:
        return self._fill_result(True, 0.0, 0.0, 0.0, OrderStatus.CANCELLED)

    def get_account_balance(self) -> float:
        return self.account_balance

    def get_positions(self) -> list[dict]:
        return []


@dataclass
class PaperTradingService:
    broker: BrokerAdapter
    signal_service: SignalService
    risk_service: RiskService
    portfolio_service: PortfolioService
    execution: ExecutionService

    _sessions: dict[uuid.UUID, PaperSession] = field(default_factory=dict)

    def create_session(
        self,
        strategy_id: uuid.UUID,
        market_ids: list[uuid.UUID],
        initial_capital: float | None = None,
    ) -> PaperSession:
        cash = (
            initial_capital
            if initial_capital is not None
            else float(os.getenv("DEFAULT_CASH", "10000.0"))
        )
        session = PaperSession(
            id=uuid.uuid4(),
            strategy_id=strategy_id,
            market_ids=market_ids,
            status=PaperSessionStatus.CREATED,
            initial_capital=cash,
            current_capital=cash,
        )
        self._sessions[session.id] = session
        return session

    def start_session(self, session_id: uuid.UUID) -> PaperSession:
        session = self._get_session(session_id)
        session.status = PaperSessionStatus.RUNNING
        session.start_time = datetime.now(UTC)
        return session

    def pause_session(self, session_id: uuid.UUID) -> PaperSession:
        session = self._get_session(session_id)
        session.status = PaperSessionStatus.PAUSED
        return session

    def stop_session(self, session_id: uuid.UUID) -> PaperSession:
        session = self._get_session(session_id)
        session.status = PaperSessionStatus.STOPPED
        session.end_time = datetime.now(UTC)
        for pos in session.positions.values():
            pnl = self.portfolio_service.compute_pnl(pos, pos.current_price)
            session.current_capital += pnl
        return session

    def process_candle(
        self,
        session_id: uuid.UUID,
        market_id: uuid.UUID,
        close_price: float,
        candle_time: datetime,
    ) -> PaperSession:
        session = self._get_session(session_id)
        if session.status != PaperSessionStatus.RUNNING:
            return session

        signals = self.signal_service.get_active_signals(market_id)
        for signal in signals:
            if signal.strategy_id != session.strategy_id:
                continue
            risk = self.risk_service.assess_trade(
                price=close_price,
                confidence=signal.confidence,
                atr=close_price * 0.01,
                account_equity=session.current_capital,
            )
            if risk.kelly_fraction <= 0:
                continue
            qty = self.portfolio_service.size_position(
                cash=session.current_capital,
                confidence=signal.confidence,
            )
            if qty <= 0:
                continue
            side = "buy" if signal.direction.value == "long" else "sell"
            order = self.execution.create_market_order(market_id, side, qty)
            fill = self.broker.place_market_order(market_id, side, qty, close_price=close_price)
            if fill.filled:
                fill_price = fill.fill_price
                trade = Trade(
                    signal_id=signal.id,
                    market_id=market_id,
                    side=TradeSide.BUY if side == "buy" else TradeSide.SELL,
                    quantity=fill.fill_quantity,
                    price=fill_price,
                )
                session.trades.append(trade)
                position = Position(
                    market_id=market_id,
                    quantity=fill.fill_quantity if side == "buy" else -fill.fill_quantity,
                    entry_price=fill_price,
                    current_price=close_price,
                    pnl=0.0,
                )
                session.positions[market_id] = position
                if fill.status == "partial":
                    session.open_orders.append(order)
                else:
                    session.filled_orders.append(order)

        equity = session.current_capital
        for pos in session.positions.values():
            pnl = self.portfolio_service.compute_pnl(pos, close_price)
            equity += pnl
        session.equity_curve.append((candle_time, equity))
        return session

    def get_session(self, session_id: uuid.UUID) -> PaperSession | None:
        return self._sessions.get(session_id)

    def list_sessions(self) -> list[PaperSession]:
        return list(self._sessions.values())

    def _get_session(self, session_id: uuid.UUID) -> PaperSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found")
        return session


@dataclass
class DeviationAnalysisService:
    def compare_metrics(
        self,
        backtest_sharpe: float,
        paper_sharpe: float,
        backtest_max_dd: float,
        paper_max_dd: float,
        backtest_win_rate: float,
        paper_win_rate: float,
    ) -> dict[str, float | str]:
        deviations: dict[str, float | str] = {}
        deviations["sharpe_deviation"] = round(paper_sharpe - backtest_sharpe, 4)
        deviations["max_dd_deviation"] = round(paper_max_dd - backtest_max_dd, 4)
        deviations["win_rate_deviation"] = round(paper_win_rate - backtest_win_rate, 4)
        tolerance = 0.5
        warnings: list[str] = []
        if abs(deviations["sharpe_deviation"]) > tolerance:
            warnings.append(f"Sharpe ratio deviates by {deviations['sharpe_deviation']:.2f}")
        if abs(deviations["max_dd_deviation"]) > tolerance:
            warnings.append(f"Max drawdown deviates by {deviations['max_dd_deviation']:.2f}")
        if abs(deviations["win_rate_deviation"]) > tolerance:
            warnings.append(f"Win rate deviates by {deviations['win_rate_deviation']:.2f}")
        deviations["warnings"] = "; ".join(warnings) if warnings else "within tolerance"
        deviations["status"] = "divergent" if warnings else "aligned"
        return deviations

    def compute_corridor(
        self,
        backtest_returns: list[float],
        paper_returns: list[float],
    ) -> dict[str, float]:
        n = min(len(backtest_returns), len(paper_returns))
        if n < 2:
            return {"correlation": 0.0, "rmse": 0.0}
        b = backtest_returns[:n]
        p = paper_returns[:n]
        mean_b = sum(b) / n
        mean_p = sum(p) / n
        num = sum((b[i] - mean_b) * (p[i] - mean_p) for i in range(n))
        den_b = sum((x - mean_b) ** 2 for x in b)
        den_p = sum((x - mean_p) ** 2 for x in p)
        corr = num / ((den_b * den_p) ** 0.5) if den_b > 0 and den_p > 0 else 0.0
        rmse = (sum((b[i] - p[i]) ** 2 for i in range(n)) / n) ** 0.5
        return {"correlation": round(corr, 4), "rmse": round(rmse, 4)}

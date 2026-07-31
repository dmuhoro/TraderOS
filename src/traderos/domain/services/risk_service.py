from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import date
from datetime import datetime
from typing import NamedTuple

from traderos.domain.entities import Position
from traderos.domain.ports import AuditPort
from traderos.domain.ports import MetricsPort
from traderos.domain.services.reconciliation_service import PersistentKillSwitch


class RiskAssessment(NamedTuple):
    kelly_fraction: float
    suggested_stop_loss: float
    suggested_take_profit: float
    risk_per_unit: float
    max_risk_amount: float


class PortfolioRisk(NamedTuple):
    var_95: float
    max_drawdown: float
    concentration_risk: list[tuple[uuid.UUID, float]]
    num_over_limit: int


class TradeVerdict(NamedTuple):
    allowed: bool
    reason: str


@dataclass
class KillSwitch:
    consecutive_failures: int = 0
    max_consecutive_failures: int = 5
    daily_loss_limit: float = float("inf")
    daily_realized_pnl: float = 0.0
    _current_day: date = field(default_factory=lambda: datetime.now(UTC).date())
    circuit_open: bool = False

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_consecutive_failures:
            self.circuit_open = True

    def record_success(self) -> None:
        self.consecutive_failures = 0

    def record_realized_pnl(self, pnl: float) -> None:
        today = datetime.now(UTC).date()
        if today != self._current_day:
            self._current_day = today
            self.daily_realized_pnl = 0.0
        self.daily_realized_pnl += pnl

    def can_trade(self) -> TradeVerdict:
        if self.circuit_open:
            return TradeVerdict(False, "Circuit breaker open")
        if self.consecutive_failures >= self.max_consecutive_failures:
            return TradeVerdict(False, f"{self.consecutive_failures} consecutive failures")
        if abs(self.daily_realized_pnl) >= self.daily_loss_limit:
            return TradeVerdict(False, f"Daily loss limit reached: {self.daily_realized_pnl:.2f}")
        return TradeVerdict(True, "")

    def engage(self) -> None:
        """Operator kill switch: block all trading immediately."""
        self.circuit_open = True

    def disengage(self) -> None:
        """Clear the operator kill switch and failure counters."""
        self.circuit_open = False
        self.consecutive_failures = 0

    def reset(self) -> None:
        self.consecutive_failures = 0
        self.daily_realized_pnl = 0.0
        self.circuit_open = False


@dataclass
class RiskService:
    max_position_size: float = 0.25
    max_leverage: float = 2.0
    max_drawdown_limit: float = 0.20
    var_confidence: float = 1.645
    kill_switch: KillSwitch = field(default_factory=KillSwitch)
    persistent_kill_switch: PersistentKillSwitch | None = None
    max_positions_total: int = 10
    audit: AuditPort | None = None
    metrics: MetricsPort | None = None

    def can_trade(self, positions: list[Position]) -> TradeVerdict:
        verdict = self.kill_switch.can_trade()
        if not verdict.allowed:
            if self.audit:
                self.audit.record("risk.kill_switch", "system", "trading", verdict.reason)
            if self.metrics:
                self.metrics.counter("circuit_breaker.tripped", 1.0)
            return verdict
        if self.persistent_kill_switch is not None and not self.persistent_kill_switch.can_trade():
            reason = "Persistent kill switch engaged"
            if self.audit:
                self.audit.record("risk.kill_switch", "system", "trading", reason)
            if self.metrics:
                self.metrics.counter("circuit_breaker.tripped", 1.0)
            return TradeVerdict(False, reason)
        if len(positions) >= self.max_positions_total:
            reason = f"Max positions ({self.max_positions_total}) reached"
            if self.audit:
                self.audit.record("risk.position_limit", "system", "trading", reason)
            return TradeVerdict(False, reason)
        return TradeVerdict(True, "")

    def record_realized_pnl(self, pnl: float) -> None:
        self.kill_switch.record_realized_pnl(pnl)
        if self.persistent_kill_switch is not None:
            self.persistent_kill_switch.record_realized_pnl(pnl)

    def assess_trade(
        self,
        price: float,
        confidence: float,
        atr: float,
        account_equity: float,
        win_rate: float = 0.5,
    ) -> RiskAssessment:
        if win_rate <= 0 or win_rate >= 1:
            return RiskAssessment(
                kelly_fraction=0.0,
                suggested_stop_loss=price - atr * 2,
                suggested_take_profit=price + atr * 3,
                risk_per_unit=price - (price - atr * 2),
                max_risk_amount=account_equity * 0.02,
            )
        b = win_rate / (1 - win_rate)
        kelly = (b * confidence - (1 - confidence)) / b
        kelly = max(0.0, min(kelly, self.max_position_size))

        stop_loss = price - atr * 2
        take_profit = price + atr * 3
        risk_per_unit = price - stop_loss
        max_risk = account_equity * 0.02

        return RiskAssessment(
            kelly_fraction=kelly,
            suggested_stop_loss=stop_loss,
            suggested_take_profit=take_profit,
            risk_per_unit=risk_per_unit,
            max_risk_amount=max_risk,
        )

    def compute_var(self, positions: list[Position]) -> float:
        if not positions:
            return 0.0
        total_value = sum(p.quantity * p.current_price for p in positions)
        if total_value == 0:
            return 0.0
        weights = [(p.quantity * p.current_price) / total_value for p in positions]
        returns = [
            (p.current_price - p.entry_price) / p.entry_price if p.entry_price else 0.0
            for p in positions
        ]
        mean_return = sum(w * r for w, r in zip(weights, returns, strict=False))
        variance = sum(w * (r - mean_return) ** 2 for w, r in zip(weights, returns, strict=False))
        std = math.sqrt(variance) if variance > 0 else 0.0
        return self.var_confidence * std * total_value

    def compute_max_drawdown(self, equity_curve: list[float]) -> float:
        if not equity_curve:
            return 0.0
        peak = equity_curve[0]
        max_dd = 0.0
        for value in equity_curve:
            peak = max(peak, value)
            dd = (peak - value) / peak
            max_dd = max(max_dd, dd)
        return max_dd

    def check_concentration(
        self,
        positions: list[Position],
    ) -> PortfolioRisk:
        if not positions:
            return PortfolioRisk(
                var_95=0.0, max_drawdown=0.0, concentration_risk=[], num_over_limit=0
            )
        total = sum(p.quantity * p.current_price for p in positions)
        concentrations: list[tuple[uuid.UUID, float]] = []
        over = 0
        for p in positions:
            pct = (p.quantity * p.current_price) / total
            concentrations.append((p.market_id, pct))
            if pct > self.max_position_size:
                over += 1
        var = self.compute_var(positions)
        return PortfolioRisk(
            var_95=var,
            max_drawdown=self.max_drawdown_limit,
            concentration_risk=concentrations,
            num_over_limit=over,
        )

    def enforce_limits(
        self,
        position: Position,
        portfolio_risk: PortfolioRisk,
    ) -> bool:
        if portfolio_risk.num_over_limit > 0:
            return False
        drawdown_limit = self.max_drawdown_limit * position.quantity * position.entry_price
        return not (position.pnl < 0 and abs(position.pnl) > drawdown_limit)

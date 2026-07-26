from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import NamedTuple

from traderos.domain.entities import Position


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


@dataclass
class RiskService:
    max_position_size: float = 0.25
    max_leverage: float = 2.0
    max_drawdown_limit: float = 0.20
    var_confidence: float = 1.645

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

"""Historical portfolio risk statistics using exact decimal money inputs."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from statistics import NormalDist


@dataclass(frozen=True)
class ValueAtRisk:
    value: Decimal
    confidence: float
    observations: int


@dataclass(frozen=True)
class Drawdown:
    amount: Decimal
    percent: Decimal
    peak_at: datetime | None
    trough_at: datetime | None
    recovered_at: datetime | None


class RiskMetrics:
    """Pure historical risk calculations; policy parameters are mandatory."""

    @staticmethod
    def historical_simulation_var(
        pnl: list[Decimal], *, confidence: float, lookback: int
    ) -> ValueAtRisk:
        sample = _validate_sample(pnl, confidence, lookback)
        # Loss is the negative of PnL. Nearest-rank empirical quantile is
        # deterministic and does not interpolate unobserved outcomes.
        ordered_losses = sorted((-item for item in sample), reverse=True)
        rank = math.ceil(confidence * len(ordered_losses))
        return ValueAtRisk(ordered_losses[rank - 1], confidence, len(sample))

    @staticmethod
    def parametric_var(pnl: list[Decimal], *, confidence: float, lookback: int) -> ValueAtRisk:
        sample = _validate_sample(pnl, confidence, lookback)
        mean = sum(sample, Decimal(0)) / Decimal(len(sample))
        variance = sum(((value - mean) ** 2 for value in sample), Decimal(0)) / Decimal(len(sample))
        z_score = Decimal(str(NormalDist().inv_cdf(confidence)))
        value = -(mean - z_score * variance.sqrt())
        return ValueAtRisk(value, confidence, len(sample))

    @staticmethod
    def maximum_drawdown(equity: list[tuple[datetime, Decimal]]) -> Drawdown:
        if not equity:
            return Drawdown(Decimal(0), Decimal(0), None, None, None)
        for _, value in equity:
            _validate_money(value)
        if any(equity[i][0] > equity[i + 1][0] for i in range(len(equity) - 1)):
            raise ValueError("equity observations must be ordered by timestamp")

        peak_value = equity[0][1]
        peak_at = equity[0][0]
        worst_amount = Decimal(0)
        worst_peak: Decimal | None = None
        worst_peak_at: datetime | None = None
        trough_at: datetime | None = None
        trough_index = -1
        for index, (timestamp, value) in enumerate(equity):
            if value > peak_value:
                peak_value, peak_at = value, timestamp
            amount = peak_value - value
            if amount > worst_amount:
                worst_amount = amount
                worst_peak, worst_peak_at = peak_value, peak_at
                trough_at, trough_index = timestamp, index

        if worst_amount == 0 or worst_peak is None or worst_peak <= 0:
            if worst_amount and worst_peak is not None and worst_peak <= 0:
                raise ValueError("drawdown percent requires a positive peak equity")
            return Drawdown(Decimal(0), Decimal(0), None, None, None)
        recovered_at = next(
            (at for at, value in equity[trough_index + 1 :] if value >= worst_peak), None
        )
        return Drawdown(
            amount=worst_amount,
            percent=worst_amount / worst_peak,
            peak_at=worst_peak_at,
            trough_at=trough_at,
            recovered_at=recovered_at,
        )


def _validate_sample(pnl: list[Decimal], confidence: float, lookback: int) -> list[Decimal]:
    if not math.isfinite(confidence) or not 0 < confidence < 1:
        raise ValueError("confidence must be finite and strictly between 0 and 1")
    if lookback < 1:
        raise ValueError("lookback must be positive")
    if len(pnl) < lookback:
        raise ValueError("lookback exceeds available PnL observations")
    sample = pnl[-lookback:]
    for value in sample:
        _validate_money(value)
    return sample


def _validate_money(value: object) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("risk metric inputs must be finite Decimal money values")

"""Exact arithmetic for per-unit historical market risk metrics."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING
from decimal import Decimal
from itertools import pairwise

CONFIDENCE_95 = Decimal("0.95")
MINIMUM_PRICE_OBSERVATIONS = 3


@dataclass(frozen=True)
class HistoricalVaRResult:
    """Historical VaR in per-unit candle-price changes."""

    value: Decimal | None
    observations: int
    refusal_reason: str | None = None


def decimal_to_wire(value: Decimal) -> str:
    """Format a finite Decimal without exponent notation or redundant zeros."""
    if not value.is_finite():
        raise ValueError("risk values must be finite decimals")
    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def historical_var(
    prices: Sequence[Decimal], confidence: Decimal = CONFIDENCE_95
) -> HistoricalVaRResult:
    """Return empirical lower-tail loss VaR from candle close prices.

    Differences are absolute per-unit candle-price changes, not percentage
    returns. The empirical tail uses the nearest-rank quantile. At the default
    95% confidence, the selected return is the worst observation in short
    histories; the API refuses histories with fewer than three closes.
    """
    if not prices:
        return HistoricalVaRResult(None, 0, "empty_series")
    if len(prices) == 1:
        return HistoricalVaRResult(None, 0, "single_observation")
    if len(prices) < MINIMUM_PRICE_OBSERVATIONS:
        return HistoricalVaRResult(None, len(prices) - 1, "insufficient_history")
    if not Decimal(0) < confidence < Decimal(1):
        raise ValueError("confidence must be between zero and one")
    if any(not price.is_finite() for price in prices):
        raise ValueError("prices must be finite decimals")

    changes = sorted(current - previous for previous, current in pairwise(prices))
    tail_size = int(
        ((Decimal(1) - confidence) * len(changes)).to_integral_value(rounding=ROUND_CEILING)
    )
    tail_change = changes[max(0, tail_size - 1)]
    return HistoricalVaRResult(max(Decimal(0), -tail_change), len(changes))

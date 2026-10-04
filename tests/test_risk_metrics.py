from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

import pytest

from traderos.domain.services.risk_metrics import RiskMetrics


def _series(values: list[str]) -> list[tuple[datetime, Decimal]]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    return [(start + timedelta(days=i), Decimal(value)) for i, value in enumerate(values)]


def test_historical_simulation_var_uses_required_lookback_and_exact_money() -> None:
    result = RiskMetrics.historical_simulation_var(
        [Decimal(v) for v in ("-1", "-2", "-3", "-4", "-5")],
        confidence=0.8,
        lookback=5,
    )
    assert result.value == Decimal(2)
    assert result.observations == 5


def test_parametric_var_is_decimal_and_requires_confidence_and_lookback() -> None:
    result = RiskMetrics.parametric_var([Decimal(1), Decimal(3)], confidence=0.5, lookback=2)
    assert result.value == Decimal(-2)
    with pytest.raises(TypeError):
        RiskMetrics.parametric_var([Decimal(1)])  # type: ignore[call-arg]


def test_drawdown_reports_amount_percent_peak_trough_and_recovery() -> None:
    points = _series(["100", "120", "90", "120"])
    result = RiskMetrics.maximum_drawdown(points)
    assert result.amount == Decimal(30)
    assert result.percent == Decimal("0.25")
    assert result.peak_at == points[1][0]
    assert result.trough_at == points[2][0]
    assert result.recovered_at == points[3][0]


@pytest.mark.parametrize("points", [[], _series(["100"])])
def test_drawdown_empty_or_single_point_is_zero(points) -> None:
    result = RiskMetrics.maximum_drawdown(points)
    assert result.amount == Decimal(0)
    assert result.percent == Decimal(0)
    assert result.peak_at is result.trough_at is result.recovered_at is None


def test_drawdown_monotonic_gain_is_zero_and_unrecovered_drawdown_is_explicit() -> None:
    gain = RiskMetrics.maximum_drawdown(_series(["100", "110", "120"]))
    assert gain.amount == gain.percent == Decimal(0)
    loss = RiskMetrics.maximum_drawdown(_series(["100", "120", "90", "80"]))
    assert loss.amount == Decimal(40)
    assert loss.percent == Decimal(1) / Decimal(3)
    assert loss.recovered_at is None


def test_var_requires_valid_confidence_and_lookback() -> None:
    with pytest.raises(ValueError):
        RiskMetrics.historical_simulation_var([Decimal(1)], confidence=1, lookback=1)
    with pytest.raises(ValueError):
        RiskMetrics.historical_simulation_var([Decimal(1)], confidence=0.95, lookback=0)


def test_drawdown_rejects_out_of_order_timestamps() -> None:
    points = _series(["100", "110"])
    points.reverse()
    with pytest.raises(ValueError, match="^equity observations must be ordered by timestamp$"):
        RiskMetrics.maximum_drawdown(points)


def test_drawdown_rejects_non_positive_peak_for_percent() -> None:
    points = _series(["-100", "-90", "-100"])
    with pytest.raises(ValueError, match="^drawdown percent requires a positive peak equity$"):
        RiskMetrics.maximum_drawdown(points)


def test_historical_var_rejects_non_finite_confidence() -> None:
    with pytest.raises(
        ValueError,
        match="^confidence must be finite and strictly between 0 and 1$",
    ):
        RiskMetrics.historical_simulation_var([Decimal(1)], confidence=float("nan"), lookback=1)


def test_historical_var_rejects_non_positive_lookback() -> None:
    with pytest.raises(ValueError, match="^lookback must be positive$"):
        RiskMetrics.historical_simulation_var([Decimal(1)], confidence=0.95, lookback=0)


def test_var_rejects_lookback_larger_than_history() -> None:
    with pytest.raises(ValueError, match="^lookback exceeds available PnL observations$"):
        RiskMetrics.historical_simulation_var([Decimal(1)], confidence=0.95, lookback=2)


def test_drawdown_rejects_non_finite_decimal_equity() -> None:
    timestamp = datetime(2025, 1, 1, tzinfo=UTC)
    with pytest.raises(
        ValueError, match="^risk metric inputs must be finite Decimal money values$"
    ):
        RiskMetrics.maximum_drawdown([(timestamp, Decimal("NaN"))])

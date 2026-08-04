"""G-06 backtest oracle: the engine must reproduce committed reference PnL.

The exit test for G-06 is: *a known strategy run through a frozen dataset
reproduces a committed reference PnL to a stated tolerance.* These references
were captured on 2026-08-04 with the cost-adjusted engine (side-aware
slippage 5bps, fee 10bps, next-bar fills). If the engine's fill/cost/indicator
semantics change, these values change and this test fails — locking every
backtest result to a reproducible baseline before a real-capital pilot.
"""

from __future__ import annotations

from backtest_oracle_dataset import MARKET_ID
from backtest_oracle_dataset import oracle_candles

from traderos.domain.services.backtesting_service import BacktestingService
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.strategy_framework import registry

# Committed reference (2026-08-04, cost-adjusted engine).
REFERENCE_FULL_RETURN = -0.094886
REFERENCE_FULL_TRADES = 55
REFERENCE_WITHHELD_RETURN = -0.028102
REFERENCE_WITHHELD_TRADES = 18
TOLERANCE = 1e-4


def _cost_adjusted() -> BacktestingService:
    return BacktestingService(execution=ExecutionService(slippage_bps=5, fee_bps=10, min_fee=0.0))


class TestBacktestOracle:
    def test_frozen_dataset_reproduces_committed_reference(self) -> None:
        candles = oracle_candles()
        strat = registry.get("moving_average_trend")()
        result, _ = _cost_adjusted().run(strat, candles, MARKET_ID)
        assert result.metrics.total_trades == REFERENCE_FULL_TRADES
        assert abs(result.metrics.total_return - REFERENCE_FULL_RETURN) < TOLERANCE

    def test_withheld_window_reproduces_committed_reference(self) -> None:
        """Out-of-sample conformance: the engine must reproduce the exact
        PnL on a withheld window too, so strategy claims on withheld data are
        grounded in the same locked engine."""
        candles = oracle_candles()[-70:]
        strat = registry.get("moving_average_trend")()
        result, _ = _cost_adjusted().run(strat, candles, MARKET_ID)
        assert result.metrics.total_trades == REFERENCE_WITHHELD_TRADES
        assert abs(result.metrics.total_return - REFERENCE_WITHHELD_RETURN) < TOLERANCE

    def test_different_data_changes_result_oracle_is_not_trivial(self) -> None:
        """Sanity: the oracle locks something real — a perturbed dataset
        must produce a different PnL, else the test would be vacuous."""
        import random
        from datetime import UTC
        from datetime import datetime
        from datetime import timedelta
        from decimal import Decimal

        from traderos.domain.entities import OHLCV
        from traderos.domain.entities import Candle
        from traderos.domain.entities import Timeframe

        rng = random.Random(999)
        price = 100.0
        start = datetime(2025, 1, 1, tzinfo=UTC)
        perturbed: list[Candle] = []
        for i in range(120):
            ret = rng.gauss(-0.001, 0.03)
            op, cl = price, max(1.0, price * (1 + ret))
            perturbed.append(
                Candle(
                    market_id=MARKET_ID,
                    ohlcv=OHLCV(
                        open=Decimal(str(round(op, 4))),
                        high=Decimal(str(round(max(op, cl) * 1.003, 4))),
                        low=Decimal(str(round(min(op, cl) * 0.997, 4))),
                        close=Decimal(str(round(cl, 4))),
                        volume=Decimal(1000),
                    ),
                    timestamp=start + timedelta(hours=i),
                    timeframe=Timeframe.HOUR_1,
                )
            )
            price = cl
        strat = registry.get("moving_average_trend")()
        result, _ = _cost_adjusted().run(strat, perturbed, MARKET_ID)
        assert abs(result.metrics.total_return - REFERENCE_FULL_RETURN) > 0.01

    def test_oracle_conformance_drill_passes(self) -> None:
        """The committed G-06 conformance drill must stay green — the engine's
        reference-PnL lock has no standing proof without it."""
        import subprocess
        import sys
        from pathlib import Path

        script = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "evidence"
            / "run_oracle_conformance.py"
        )
        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[1],
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "VERDICT: PASS" in proc.stdout

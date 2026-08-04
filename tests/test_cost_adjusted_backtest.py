from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime
from decimal import Decimal

from traderos.domain.entities import OHLCV
from traderos.domain.entities import Candle
from traderos.domain.entities import Timeframe
from traderos.domain.services.backtesting_service import BacktestingService
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.strategy_framework import MarketState
from traderos.domain.services.strategy_framework import SignalResult
from traderos.domain.services.strategy_framework import StrategyBase


class _AlwaysBuy(StrategyBase):
    def evaluate(self, state: MarketState) -> SignalResult | None:
        return SignalResult("long", 0.5, {})


def _candles(n: int, start_price: float = 100.0) -> list[Candle]:
    mid = uuid.uuid4()
    return [
        Candle(
            market_id=mid,
            ohlcv=OHLCV(
                open=Decimal(str(start_price + i)),
                high=Decimal(str(start_price + i + 1)),
                low=Decimal(str(start_price + i - 1)),
                close=Decimal(str(start_price + i)),
                volume=Decimal(1000),
            ),
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            timeframe=Timeframe.DAY_1,
        )
        for i in range(n)
    ]


class TestSideAwareSlippage:
    def test_buy_pays_up(self) -> None:
        svc = ExecutionService(slippage_bps=10)
        order = svc.create_market_order(uuid.uuid4(), "buy", 10.0)
        assert svc.process_market_order(order, 100.0).fill_price == 100.0 * 1.001

    def test_sell_receives_down(self) -> None:
        svc = ExecutionService(slippage_bps=10)
        order = svc.create_market_order(uuid.uuid4(), "sell", 10.0)
        assert svc.process_market_order(order, 100.0).fill_price == 100.0 * 0.999


class TestFeeModel:
    def test_fee_charged_on_fill(self) -> None:
        svc = ExecutionService(fee_bps=20, min_fee=0.0)
        order = svc.create_market_order(uuid.uuid4(), "buy", 10.0)
        result = svc.process_market_order(order, 100.0)
        assert result.fee == 10.0 * result.fill_price * 20 / 10000

    def test_min_fee_floor(self) -> None:
        svc = ExecutionService(fee_bps=0, min_fee=1.0)
        order = svc.create_market_order(uuid.uuid4(), "buy", 10.0)
        assert svc.process_market_order(order, 100.0).fee == 1.0

    def test_costs_make_backtest_more_conservative(self) -> None:
        candles = _candles(40, 100.0)
        no_cost = BacktestingService(execution=ExecutionService(slippage_bps=0, fee_bps=0))
        costly = BacktestingService(execution=ExecutionService(slippage_bps=5, fee_bps=10))
        r1, _ = no_cost.run(_AlwaysBuy(), candles, uuid.uuid4())
        r2, _ = costly.run(_AlwaysBuy(), candles, uuid.uuid4())
        assert r1.metrics.total_return > r2.metrics.total_return


class TestLatency:
    def test_fill_happens_on_next_bar(self) -> None:
        svc = BacktestingService(execution=ExecutionService(slippage_bps=0))
        candles = _candles(10, 100.0)
        _, steps = svc.run(_AlwaysBuy(), candles, uuid.uuid4())
        assert steps[0].order is not None, "signal bar records the order"
        assert steps[0].fill_price is None, "no same-bar fill (no look-ahead)"
        assert steps[1].fill_price is not None, "fill executes on the next bar's open"

    def test_last_bar_signal_never_fills(self) -> None:
        svc = BacktestingService(execution=ExecutionService(slippage_bps=0))
        candles = _candles(4, 100.0)
        result, _ = svc.run(_AlwaysBuy(), candles, uuid.uuid4())
        assert result.metrics.total_trades == 3, "final-bar signal is dropped, not filled"

    def test_latency_widens_buy_fill_price(self) -> None:
        svc = ExecutionService(slippage_bps=5, latency_bps=5)
        order = svc.create_market_order(uuid.uuid4(), "buy", 10.0)
        result = svc.process_market_order(order, 100.0)
        assert result.fill_price == 100.0 * (1 + 10 / 10000)

    def test_latency_lowers_sell_fill_price(self) -> None:
        svc = ExecutionService(slippage_bps=5, latency_bps=5)
        order = svc.create_market_order(uuid.uuid4(), "sell", 10.0)
        result = svc.process_market_order(order, 100.0)
        assert result.fill_price == 100.0 * (1 - 10 / 10000)

    def test_latency_makes_backtest_more_conservative(self) -> None:
        candles = _candles(40, 100.0)
        no_latency = BacktestingService(execution=ExecutionService(slippage_bps=5, fee_bps=10))
        latency = BacktestingService(
            execution=ExecutionService(slippage_bps=5, fee_bps=10, latency_bps=10)
        )
        r1, _ = no_latency.run(_AlwaysBuy(), candles, uuid.uuid4())
        r2, _ = latency.run(_AlwaysBuy(), candles, uuid.uuid4())
        assert r1.metrics.total_return > r2.metrics.total_return


class TestWalkForward:
    def test_walk_forward_splits_folds(self) -> None:
        svc = BacktestingService(execution=ExecutionService(slippage_bps=5, fee_bps=10))
        candles = _candles(30, 100.0)
        result = svc.walk_forward(_AlwaysBuy(), candles, uuid.uuid4(), n_splits=5)
        assert len(result["fold_returns"]) == 5
        assert result["positive_folds"] >= 0

    def test_walk_forward_requires_enough_candles(self) -> None:
        svc = BacktestingService(execution=ExecutionService())
        with __import__("pytest").raises(ValueError):
            svc.walk_forward(_AlwaysBuy(), _candles(5, 100.0), uuid.uuid4(), n_splits=5)


class TestWalkForwardEvidenceDrill:
    def test_walk_forward_evidence_drill_passes(self) -> None:
        """The committed G-01 evidence drill must stay green — a reproducible
        record that the cost-adjusted engine ran over a withheld out-of-sample
        window with full costs (slippage + fee + latency)."""
        import subprocess
        import sys
        from pathlib import Path

        script = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "evidence"
            / "run_walk_forward_evidence.py"
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
        assert "latency" in proc.stdout

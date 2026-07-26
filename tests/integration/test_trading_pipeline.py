from __future__ import annotations

import uuid
from datetime import datetime
from datetime import UTC
from decimal import Decimal

from traderos.domain.entities import Candle
from traderos.domain.entities import OHLCV
from traderos.domain.entities import Timeframe
from traderos.domain.services.backtesting_service import BacktestingService
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.paper_trading_service import PaperBrokerAdapter
from traderos.domain.services.paper_trading_service import PaperTradingService
from traderos.domain.services.portfolio_service import PortfolioService
from traderos.domain.services.risk_service import RiskService
from traderos.domain.services.signal_service import SignalService
from traderos.domain.services.strategy_framework import MovingAverageTrend
from traderos.domain.services.strategy_framework import SignalResult
from traderos.domain.services.strategy_framework import StrategyBase
from traderos.domain.services.strategy_framework import MarketState


def _make_candle(i: int, mid: uuid.UUID) -> Candle:
    return Candle(
        market_id=mid,
        ohlcv=OHLCV(
            open=Decimal(str(100 + i)),
            high=Decimal(str(101 + i)),
            low=Decimal(str(99 + i)),
            close=Decimal(str(100 + i)),
            volume=Decimal(1000),
        ),
        timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        timeframe=Timeframe.DAY_1,
    )


class TestTradingPipeline:
    def test_strategy_to_backtest(self) -> None:
        class AlwaysBuyStrategy(StrategyBase):
            name = "always_buy"
            version = "1.0.0"
            def evaluate(self, state: MarketState) -> SignalResult | None:
                return SignalResult("long", 0.5, {})
        strategy = AlwaysBuyStrategy()
        mid = uuid.uuid4()
        candles = [_make_candle(i, mid) for i in range(50)]
        svc = BacktestingService(execution=ExecutionService())
        result, steps = svc.run(strategy, candles, mid)
        assert result.metrics.total_return != 0.0
        assert len(steps) == 50

    def test_risk_assessment_on_backtest_metrics(self) -> None:
        risk = RiskService()
        assessment = risk.assess_trade(
            price=100.0, confidence=0.8, atr=2.0,
            account_equity=10000.0, win_rate=0.6,
        )
        assert assessment.kelly_fraction > 0
        assert assessment.suggested_stop_loss > 0

    def test_execution_order_lifecycle(self) -> None:
        svc = ExecutionService()
        mid = uuid.uuid4()
        order = svc.create_market_order(mid, "buy", 10.0)
        assert order.status.value == "pending"
        fill = svc.process_market_order(order, 100.0)
        assert fill.filled
        assert fill.fill_price > 100.0

    def test_signal_to_paper_trade(self) -> None:

        strategy_id = uuid.uuid4()
        market_id = uuid.uuid4()
        paper = PaperTradingService(
            broker=PaperBrokerAdapter(fill_probability=1.0),
            signal_service=SignalService.__new__(SignalService),
            risk_service=RiskService(),
            portfolio_service=PortfolioService.__new__(PortfolioService),
            execution=ExecutionService(),
        )
        session = paper.create_session(strategy_id, [market_id])
        paper.start_session(session.id)
        assert session.status.value == "running"

    def test_metrics_collection_after_pipeline(self) -> None:
        from traderos.infrastructure.metrics import MetricsService
        svc = MetricsService()
        svc.counter("backtests.run", 1)
        svc.counter("signals.generated", 5)
        svc.gauge("account.equity", 10500.0)
        snap = svc.snapshot()
        assert snap["backtests.run"] == 1.0
        assert snap["signals.generated"] == 5.0
        assert snap["account.equity"] == 10500.0

    def test_audit_trail_integration(self) -> None:
        from traderos.infrastructure.audit import AuditService
        svc = AuditService()
        svc.record("strategy.evaluate", "system", "MA Trend", "returned signal")
        svc.record("risk.assess", "system", "BTC/USD", "kelly=0.15")
        svc.record("execution.fill", "broker", "BTC/USD", "filled 0.1 @ 50000")
        assert len(svc.get_entries()) == 3
        assert svc.verify_chain()

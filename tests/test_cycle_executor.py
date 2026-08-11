from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime
from unittest.mock import Mock

from traderos.application.cycle_executor import CycleExecutor
from traderos.application.models import TradingMode
from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.adapters.broker_adapter import FillResult
from traderos.domain.services.backtesting_service import BacktestingService
from traderos.domain.services.backtesting_service import synthetic_candles
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.knowledge_graph_service import KnowledgeGraphService
from traderos.domain.services.research_service import ResearchService
from traderos.domain.services.strategy_framework import SignalResult
from traderos.domain.services.strategy_framework import StrategyBase
from traderos.domain.services.strategy_framework import registry as strategy_registry
from traderos.infrastructure.events import InMemoryEventBus
from traderos.infrastructure.observability import SQLiteAuditService
from traderos.infrastructure.observability import SQLiteHealthService
from traderos.infrastructure.observability import SQLiteManifestService
from traderos.infrastructure.observability import SQLiteMetricsService
from traderos.infrastructure.repositories.in_memory import InMemoryExperimentRepository
from traderos.infrastructure.repositories.in_memory import InMemoryExperimentResultRepository
from traderos.infrastructure.repositories.in_memory import InMemoryHypothesisRepository
from traderos.infrastructure.repositories.in_memory import InMemoryKnowledgeEdgeRepository
from traderos.infrastructure.repositories.in_memory import InMemoryKnowledgeNodeRepository
from traderos.infrastructure.repositories.in_memory import InMemoryLessonRepository
from traderos.infrastructure.repositories.in_memory import InMemoryObservationRepository


class _MockBroker(BrokerAdapter):
    def place_market_order(self, market_id, side, quantity, close_price=None, client_order_id=None):
        return FillResult(True, quantity, 100.0, 0.0, "filled", "ord1")

    def place_limit_order(self, market_id, side, quantity, price, close_price=None):
        return FillResult(False, 0.0, 0.0, quantity, "pending", "")

    def cancel_order(self, order_id):
        return FillResult(True, 0.0, 0.0, 0.0, "cancelled", order_id)

    def place_stop_order(self, market_id, side, quantity, stop_price, market_price=None):
        return FillResult(False, 0.0, 0.0, quantity, "pending", "")

    def place_trailing_stop_order(
        self, market_id, side, quantity, trail_percent, market_price=None
    ):
        return FillResult(False, 0.0, 0.0, quantity, "pending", "")

    def modify_order(
        self, order_id, qty=None, limit_price=None, stop_price=None, trail_percent=None
    ):
        return FillResult(True, 0.0, 0.0, 0.0, "modified", order_id)

    def get_account_balance(self):
        return 10000.0

    def get_positions(self):
        return []

    def get_open_orders(self):
        return []


class _UnfilledBroker(BrokerAdapter):
    def place_market_order(self, market_id, side, quantity, close_price=None, client_order_id=None):
        return FillResult(False, 0.0, 0.0, quantity, "rejected", "")

    def place_limit_order(self, market_id, side, quantity, price, close_price=None):
        return FillResult(False, 0.0, 0.0, quantity, "pending", "")

    def cancel_order(self, order_id):
        return FillResult(True, 0.0, 0.0, 0.0, "cancelled", order_id)

    def place_stop_order(self, market_id, side, quantity, stop_price, market_price=None):
        return FillResult(False, 0.0, 0.0, quantity, "pending", "")

    def place_trailing_stop_order(
        self, market_id, side, quantity, trail_percent, market_price=None
    ):
        return FillResult(False, 0.0, 0.0, quantity, "pending", "")

    def modify_order(
        self, order_id, qty=None, limit_price=None, stop_price=None, trail_percent=None
    ):
        return FillResult(True, 0.0, 0.0, 0.0, "modified", order_id)

    def get_account_balance(self):
        return 10000.0

    def get_positions(self):
        return []

    def get_open_orders(self):
        return []


class _BadStrat(StrategyBase):
    name = "test_bad_strat"

    def evaluate(self, state):
        msg = "intentional error"
        raise RuntimeError(msg)


class _GoodStrat(StrategyBase):
    name = "test_good_strat"

    def evaluate(self, state):
        return SignalResult("long", 0.8, {"reason": "test"})


def _make_conn():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from traderos.infrastructure.database.migration_manager import migrate

    migrate(conn)
    return conn


def _register(name, cls):
    strategy_registry._strategies[name] = cls


def _unregister(name):
    strategy_registry._strategies.pop(name, None)


def _happy_services() -> dict:
    """Mocks that let a signal run all the way to the broker submission path."""
    from datetime import timedelta

    from traderos.domain.entities.signal import Signal
    from traderos.domain.entities.signal import SignalDirection
    from traderos.domain.services.risk_service import RiskAssessment
    from traderos.domain.services.risk_service import TradeVerdict
    from traderos.domain.services.signal_service import SignalProvenance

    now = datetime.now(UTC)
    signal = Signal(
        market_id=uuid.uuid4(),
        strategy_id=uuid.uuid4(),
        direction=SignalDirection.LONG,
        confidence=0.8,
        generated_at=now,
        expires_at=now + timedelta(hours=1),
    )
    signal_service = Mock()
    signal_service.process_evaluation.return_value = SignalProvenance(
        signal=signal, strategy_name="x", indicators_used={}
    )
    risk_service = Mock()
    risk_service.can_trade.return_value = TradeVerdict(True, "")
    risk_service.kill_switch = Mock()
    risk_service.assess_trade.return_value = RiskAssessment(
        kelly_fraction=0.5,
        suggested_stop_loss=99.0,
        suggested_take_profit=102.0,
        risk_per_unit=1.0,
        max_risk_amount=200.0,
    )
    risk_service.authorize_order.return_value = TradeVerdict(True, "")
    portfolio_service = Mock()
    summary = Mock()
    summary.open_positions = []
    summary.total_equity = 10000.0
    portfolio_service.get_summary.return_value = summary
    portfolio_service.size_position.return_value = 1.0
    return {
        "signal_service": signal_service,
        "risk_service": risk_service,
        "portfolio_service": portfolio_service,
    }


def _executor(conn, **overrides) -> CycleExecutor:
    base = {
        "mode": TradingMode.PAPER,
        "signal_service": Mock(),
        "risk_service": Mock(),
        "portfolio_service": Mock(),
        "execution": Mock(),
        "analysis": Mock(),
        "broker": _MockBroker(),
        "event_bus": InMemoryEventBus(),
        "health": SQLiteHealthService(conn),
        "audit": SQLiteAuditService(conn),
        "metrics": SQLiteMetricsService(conn),
        "notifications": Mock(),
        "run_manifest": SQLiteManifestService(conn),
    }
    base.update(overrides)
    return CycleExecutor(**base)


class TestCycleExecutor:
    def test_backtest_mode_runs_strategies(self) -> None:
        conn = _make_conn()
        _register("test_backtest_good", _GoodStrat)
        try:
            executor = CycleExecutor(
                mode=TradingMode.BACKTEST,
                signal_service=Mock(),
                risk_service=Mock(),
                portfolio_service=Mock(),
                execution=Mock(),
                analysis=Mock(),
                broker=_MockBroker(),
                event_bus=InMemoryEventBus(),
                health=SQLiteHealthService(conn),
                audit=SQLiteAuditService(conn),
                metrics=SQLiteMetricsService(conn),
                notifications=Mock(),
                run_manifest=SQLiteManifestService(conn),
                backtest=BacktestingService(execution=ExecutionService()),
            )
            mid = uuid.uuid4()
            result = executor.run(mid, 100.0)
            assert result.market_id == mid
            assert result.signals > 0
            assert result.trades > 0
            assert not any("test_backtest_good" in e for e in result.errors)
        finally:
            _unregister("test_backtest_good")
        conn.close()

    def test_backtest_mode_without_service_records_error(self) -> None:
        conn = _make_conn()
        executor = CycleExecutor(
            mode=TradingMode.BACKTEST,
            signal_service=Mock(),
            risk_service=Mock(),
            portfolio_service=Mock(),
            execution=Mock(),
            analysis=Mock(),
            broker=_MockBroker(),
            event_bus=InMemoryEventBus(),
            health=SQLiteHealthService(conn),
            audit=SQLiteAuditService(conn),
            metrics=SQLiteMetricsService(conn),
            notifications=Mock(),
            run_manifest=SQLiteManifestService(conn),
        )
        result = executor.run(uuid.uuid4(), 100.0)
        assert len(result.errors) == 1
        assert "BacktestingService is not available" in result.errors[0]
        conn.close()

    def test_cycle_analysis_event_publishes_regime(self) -> None:
        conn = _make_conn()
        data_ingestion = Mock()
        data_ingestion.fetch_candles.return_value = synthetic_candles(
            count=210, market_id=uuid.uuid4()
        )
        analysis = Mock()
        analysis.compute_sma.return_value = []
        analysis.compute_atr.return_value = []
        analysis.compute_bollinger_bands.return_value = Mock(upper=[], lower=[])
        event_bus = InMemoryEventBus()
        analysis_events: list = []
        event_bus.subscribe("cycle.analysis", analysis_events.append)
        executor = CycleExecutor(
            mode=TradingMode.PAPER,
            signal_service=Mock(),
            risk_service=Mock(),
            portfolio_service=Mock(),
            execution=Mock(),
            analysis=analysis,
            broker=_MockBroker(),
            event_bus=event_bus,
            health=SQLiteHealthService(conn),
            audit=SQLiteAuditService(conn),
            metrics=SQLiteMetricsService(conn),
            notifications=Mock(),
            run_manifest=SQLiteManifestService(conn),
            data_ingestion=data_ingestion,
        )
        executor.run(uuid.uuid4(), 100.0)
        assert analysis_events, "cycle.analysis event not published"
        payload = analysis_events[0].payload
        assert payload["regime"] == "trending_bullish"
        assert isinstance(payload["breakout_events"], list)
        conn.close()

    def test_post_trade_records_knowledge_and_research(self) -> None:
        from datetime import timedelta

        from traderos.domain.entities.signal import Signal
        from traderos.domain.entities.signal import SignalDirection
        from traderos.domain.services.analysis_service import AnalysisService
        from traderos.domain.services.risk_service import KillSwitch
        from traderos.domain.services.risk_service import RiskAssessment
        from traderos.domain.services.risk_service import TradeVerdict
        from traderos.domain.services.signal_service import SignalProvenance

        conn = _make_conn()
        _register("test_evidence_good", _GoodStrat)
        try:
            data_ingestion = Mock()
            data_ingestion.fetch_candles.return_value = synthetic_candles(
                count=25, market_id=uuid.uuid4()
            )
            now = datetime.now(UTC)
            signal = Signal(
                market_id=uuid.uuid4(),
                strategy_id=uuid.uuid4(),
                direction=SignalDirection.LONG,
                confidence=0.8,
                generated_at=now,
                expires_at=now + timedelta(hours=1),
            )
            provenance = SignalProvenance(signal=signal, strategy_name="x", indicators_used={})
            signal_service = Mock()
            signal_service.process_evaluation.return_value = provenance
            risk_service = Mock()
            risk_service.can_trade.return_value = TradeVerdict(True, "")
            risk_service.kill_switch = KillSwitch()
            risk_service.assess_trade.return_value = RiskAssessment(
                kelly_fraction=0.5,
                suggested_stop_loss=99.0,
                suggested_take_profit=102.0,
                risk_per_unit=1.0,
                max_risk_amount=200.0,
            )
            portfolio_service = Mock()
            summary = Mock()
            summary.open_positions = []
            summary.total_equity = 10000.0
            portfolio_service.get_summary.return_value = summary
            portfolio_service.size_position.return_value = 1.0

            knowledge_graph = KnowledgeGraphService(
                nodes=InMemoryKnowledgeNodeRepository(),
                edges=InMemoryKnowledgeEdgeRepository(),
            )
            research = ResearchService(
                observations=InMemoryObservationRepository(),
                hypotheses=InMemoryHypothesisRepository(),
                experiments=InMemoryExperimentRepository(),
                results=InMemoryExperimentResultRepository(),
                lessons=InMemoryLessonRepository(),
            )

            executor = CycleExecutor(
                mode=TradingMode.PAPER,
                signal_service=signal_service,
                risk_service=risk_service,
                portfolio_service=portfolio_service,
                execution=Mock(),
                analysis=AnalysisService(),
                broker=_MockBroker(),
                event_bus=InMemoryEventBus(),
                health=SQLiteHealthService(conn),
                audit=SQLiteAuditService(conn),
                metrics=SQLiteMetricsService(conn),
                notifications=Mock(),
                run_manifest=SQLiteManifestService(conn),
                data_ingestion=data_ingestion,
                knowledge_graph=knowledge_graph,
                research=research,
            )
            result = executor.run(uuid.uuid4(), 100.0)
            assert result.errors == []
            assert result.trades > 0
            assert research.observations.list(), "no research observation recorded"
            assert knowledge_graph.nodes.list(), "no knowledge nodes recorded"
            assert knowledge_graph.edges.list(), "no knowledge edges recorded"
        finally:
            _unregister("test_evidence_good")
        conn.close()

    def test_no_data_ingestion_runs_cleanly(self) -> None:
        conn = _make_conn()
        executor = CycleExecutor(
            mode=TradingMode.PAPER,
            signal_service=Mock(),
            risk_service=Mock(),
            portfolio_service=Mock(),
            execution=Mock(),
            analysis=Mock(),
            broker=_MockBroker(),
            event_bus=InMemoryEventBus(),
            health=SQLiteHealthService(conn),
            audit=SQLiteAuditService(conn),
            metrics=SQLiteMetricsService(conn),
            notifications=Mock(),
            run_manifest=SQLiteManifestService(conn),
        )
        mid = uuid.uuid4()
        result = executor.run(mid, 100.0, candle_time=datetime.now(UTC))
        assert result.market_id == mid
        assert result.timestamp is not None
        conn.close()

    def test_strategy_raise_caught_by_inner_except(self) -> None:
        conn = _make_conn()
        _register("test_bad_strat", _BadStrat)
        try:
            executor = CycleExecutor(
                mode=TradingMode.PAPER,
                signal_service=Mock(),
                risk_service=Mock(),
                portfolio_service=Mock(),
                execution=Mock(),
                analysis=Mock(),
                broker=_MockBroker(),
                event_bus=InMemoryEventBus(),
                health=SQLiteHealthService(conn),
                audit=SQLiteAuditService(conn),
                metrics=SQLiteMetricsService(conn),
                notifications=Mock(),
                run_manifest=SQLiteManifestService(conn),
            )
            result = executor.run(uuid.uuid4(), 100.0)
            assert len(result.errors) > 0
            assert any("test_bad_strat" in e for e in result.errors)
        finally:
            _unregister("test_bad_strat")
        conn.close()

    def test_data_ingestion_raise_caught_by_outer_except(self) -> None:
        conn = _make_conn()
        data_ingestion = Mock()
        data_ingestion.fetch_candles.side_effect = RuntimeError("outer failure")
        executor = CycleExecutor(
            mode=TradingMode.PAPER,
            signal_service=Mock(),
            risk_service=Mock(),
            portfolio_service=Mock(),
            execution=Mock(),
            analysis=Mock(),
            broker=_MockBroker(),
            event_bus=InMemoryEventBus(),
            health=SQLiteHealthService(conn),
            audit=SQLiteAuditService(conn),
            metrics=SQLiteMetricsService(conn),
            notifications=Mock(),
            run_manifest=SQLiteManifestService(conn),
            data_ingestion=data_ingestion,
        )
        result = executor.run(uuid.uuid4(), 100.0)
        assert len(result.errors) > 0
        assert "outer failure" in result.errors[0]
        conn.close()

    def test_cash_balance_live_mode(self) -> None:
        conn = _make_conn()
        executor = CycleExecutor(
            mode=TradingMode.LIVE,
            signal_service=Mock(),
            risk_service=Mock(),
            portfolio_service=Mock(),
            execution=Mock(),
            analysis=Mock(),
            broker=_MockBroker(),
            event_bus=InMemoryEventBus(),
            health=SQLiteHealthService(conn),
            audit=SQLiteAuditService(conn),
            metrics=SQLiteMetricsService(conn),
            notifications=Mock(),
            run_manifest=SQLiteManifestService(conn),
        )
        assert executor._cash_balance() == 10000.0
        conn.close()

    def test_cash_balance_paper_mode(self) -> None:
        conn = _make_conn()
        executor = CycleExecutor(
            mode=TradingMode.PAPER,
            signal_service=Mock(),
            risk_service=Mock(),
            portfolio_service=Mock(),
            execution=Mock(),
            analysis=Mock(),
            broker=_MockBroker(),
            event_bus=InMemoryEventBus(),
            health=SQLiteHealthService(conn),
            audit=SQLiteAuditService(conn),
            metrics=SQLiteMetricsService(conn),
            notifications=Mock(),
            run_manifest=SQLiteManifestService(conn),
            default_cash=50000.0,
        )
        assert executor._cash_balance() == 50000.0
        conn.close()

    def test_candles_processed_with_data_ingestion(self) -> None:
        from datetime import timedelta
        from decimal import Decimal

        conn = _make_conn()
        data_ingestion = Mock()
        from traderos.domain.entities.candle import Candle
        from traderos.domain.entities.signal import Signal
        from traderos.domain.entities.signal import SignalDirection
        from traderos.domain.entities.value_objects import OHLCV
        from traderos.domain.entities.value_objects import Timeframe
        from traderos.domain.services.analysis_service import AnalysisService
        from traderos.domain.services.risk_service import KillSwitch
        from traderos.domain.services.risk_service import RiskAssessment
        from traderos.domain.services.risk_service import TradeVerdict
        from traderos.domain.services.signal_service import SignalProvenance

        data_ingestion.fetch_candles.return_value = [
            Candle(
                market_id=uuid.uuid4(),
                ohlcv=OHLCV(
                    Decimal("100.0"),
                    Decimal("101.0"),
                    Decimal("99.0"),
                    Decimal("100.5"),
                    Decimal("1000.0"),
                ),
                timestamp=datetime.now(UTC),
                timeframe=Timeframe.MINUTE_1,
            )
        ] * 25

        now = datetime.now(UTC)
        signal = Signal(
            market_id=uuid.uuid4(),
            strategy_id=uuid.uuid4(),
            direction=SignalDirection.LONG,
            confidence=0.8,
            generated_at=now,
            expires_at=now + timedelta(hours=1),
        )
        provenance = SignalProvenance(signal=signal, strategy_name="x", indicators_used={})
        signal_service = Mock()
        signal_service.process_evaluation.return_value = provenance

        risk_service = Mock()
        risk_service.can_trade.return_value = TradeVerdict(True, "")
        risk_service.kill_switch = KillSwitch()
        risk_service.assess_trade.return_value = RiskAssessment(
            kelly_fraction=0.5,
            suggested_stop_loss=99.0,
            suggested_take_profit=102.0,
            risk_per_unit=1.0,
            max_risk_amount=200.0,
        )

        portfolio_service = Mock()
        summary = Mock()
        summary.open_positions = []
        summary.total_equity = 10000.0
        portfolio_service.get_summary.return_value = summary
        portfolio_service.size_position.return_value = 1.0

        executor = CycleExecutor(
            mode=TradingMode.PAPER,
            signal_service=signal_service,
            risk_service=risk_service,
            portfolio_service=portfolio_service,
            execution=Mock(),
            analysis=AnalysisService(),
            broker=_MockBroker(),
            event_bus=InMemoryEventBus(),
            health=SQLiteHealthService(conn),
            audit=SQLiteAuditService(conn),
            metrics=SQLiteMetricsService(conn),
            notifications=Mock(),
            run_manifest=SQLiteManifestService(conn),
            data_ingestion=data_ingestion,
        )
        result = executor.run(uuid.uuid4(), 100.0)
        assert result.errors == []
        assert result.trades > 0
        assert portfolio_service.fill_trade.called
        conn.close()

    def test_live_data_gap_blocks_trading(self) -> None:
        conn = _make_conn()
        mid = uuid.uuid4()
        data_ingestion = Mock()
        data_ingestion.fetch_candles.return_value = []
        notifications = Mock()
        executor = _executor(
            conn,
            mode=TradingMode.LIVE,
            data_ingestion=data_ingestion,
            notifications=notifications,
        )
        result = executor.run(mid, 100.0)
        assert result.errors == [f"{mid}: no market data — trading blocked"]
        assert result.trades == 0
        notifications.critical.assert_called_once()
        conn.close()

    def test_sma_50_computed_with_enough_candles(self) -> None:
        from traderos.domain.services.analysis_service import AnalysisService

        conn = _make_conn()
        _register("test_sma50_strat", _GoodStrat)
        try:
            data_ingestion = Mock()
            data_ingestion.fetch_candles.return_value = synthetic_candles(
                count=60, market_id=uuid.uuid4()
            )
            executor = _executor(
                conn,
                data_ingestion=data_ingestion,
                analysis=AnalysisService(),
                **_happy_services(),
            )
            result = executor.run(uuid.uuid4(), 100.0)
            assert result.errors == []
            assert result.trades > 0
        finally:
            _unregister("test_sma50_strat")
        conn.close()

    def test_none_provenance_skips_signal(self) -> None:
        conn = _make_conn()
        _register("test_provenance_none", _GoodStrat)
        try:
            signal_service = Mock()
            signal_service.process_evaluation.return_value = None
            executor = _executor(conn, signal_service=signal_service)
            result = executor.run(uuid.uuid4(), 100.0)
            assert result.errors == []
            assert result.signals == 0
            assert result.trades == 0
        finally:
            _unregister("test_provenance_none")
        conn.close()

    def test_zero_kelly_skips_order(self) -> None:
        from traderos.domain.services.risk_service import RiskAssessment

        conn = _make_conn()
        _register("test_kelly_zero", _GoodStrat)
        try:
            services = _happy_services()
            services["risk_service"].assess_trade.return_value = RiskAssessment(
                kelly_fraction=0.0,
                suggested_stop_loss=99.0,
                suggested_take_profit=102.0,
                risk_per_unit=1.0,
                max_risk_amount=200.0,
            )
            executor = _executor(conn, **services)
            result = executor.run(uuid.uuid4(), 100.0)
            assert result.errors == []
            assert result.trades == 0
            services["portfolio_service"].open_trade.assert_not_called()
        finally:
            _unregister("test_kelly_zero")
        conn.close()

    def test_zero_qty_skips_order(self) -> None:
        conn = _make_conn()
        _register("test_qty_zero", _GoodStrat)
        try:
            services = _happy_services()
            services["portfolio_service"].size_position.return_value = 0.0
            executor = _executor(conn, **services)
            result = executor.run(uuid.uuid4(), 100.0)
            assert result.errors == []
            assert result.trades == 0
            services["portfolio_service"].open_trade.assert_not_called()
        finally:
            _unregister("test_qty_zero")
        conn.close()

    def test_unfilled_fill_records_kill_switch_failure(self) -> None:
        conn = _make_conn()
        _register("test_unfilled", _GoodStrat)
        try:
            services = _happy_services()
            executor = _executor(conn, broker=_UnfilledBroker(), **services)
            result = executor.run(uuid.uuid4(), 100.0)
            assert result.trades == 0
            services["risk_service"].kill_switch.record_failure.assert_called_once()
        finally:
            _unregister("test_unfilled")
        conn.close()

    def test_retail_unfilled_order_records_failure_and_rejected(self) -> None:
        conn = _make_conn()
        services = _happy_services()
        metrics = SQLiteMetricsService(conn)
        executor = _executor(conn, broker=_UnfilledBroker(), metrics=metrics, **services)
        result = executor.submit_retail_order(uuid.uuid4(), "buy", 2.0, 100.0, user_id="retail-1")
        assert result.allowed is True
        assert result.order_id is None
        services["risk_service"].kill_switch.record_failure.assert_called_once()
        assert metrics.get_counter("retail.orders.rejected") == 1
        conn.close()

    def test_backtest_uses_data_ingestion_candles(self) -> None:
        conn = _make_conn()
        _register("test_backtest_di", _GoodStrat)
        try:
            data_ingestion = Mock()
            data_ingestion.fetch_candles.return_value = synthetic_candles(
                count=60, market_id=uuid.uuid4()
            )
            executor = _executor(
                conn,
                mode=TradingMode.BACKTEST,
                data_ingestion=data_ingestion,
                backtest=BacktestingService(execution=ExecutionService()),
            )
            result = executor.run(uuid.uuid4(), 100.0)
            data_ingestion.fetch_candles.assert_called_once()
            assert result.signals > 0
            assert result.trades > 0
            assert result.errors == []
        finally:
            _unregister("test_backtest_di")
        conn.close()

    def test_backtest_skips_unknown_strategy_template(self) -> None:
        conn = _make_conn()
        executor = _executor(
            conn,
            mode=TradingMode.BACKTEST,
            backtest=BacktestingService(execution=ExecutionService()),
            enabled_strategies=lambda: [("ghost", "ghost_template", {})],
        )
        result = executor.run(uuid.uuid4(), 100.0)
        assert result.errors == []
        assert result.signals == 0
        assert result.trades == 0
        conn.close()

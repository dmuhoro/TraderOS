"""Programme A — Core Loop Integrity invariant tests.

Each test pins one invariant from docs/engineering/CORE_LOOP_TRUTH.md:
  I1  every accepted fill -> one FILLED Trade + one Position record
  I2  position close reports realized PnL to the kill switches
  I3  trade transitions follow _VALID_TRANSITIONS (PENDING->FILLED rejected)
  I5  sizing returns share quantity, never dollars
  I6  every registered built-in strategy can fire on the cycle's indicator set
  I8  cycle metrics are per-cycle and truthful
  I9  MarketState uses real candle data when candles exist; fallbacks fabricate no signals
Regressions pinned here: D1 (fill_trade never called), D2 (empty order_id fill crash),
D3 (dollar sizing), D4 (realized PnL never reported), D5 (dead strategies), D6 (broken metrics).
"""

from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from unittest.mock import Mock

import pytest

from traderos.application.cycle_executor import CycleExecutor
from traderos.application.models import TradingMode
from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.adapters.broker_adapter import FillResult
from traderos.domain.entities import Position
from traderos.domain.entities import Signal
from traderos.domain.entities import SignalDirection
from traderos.domain.entities.candle import Candle
from traderos.domain.entities.trade import InvalidTradeTransitionError
from traderos.domain.entities.trade import Trade
from traderos.domain.entities.trade import TradeSide
from traderos.domain.entities.trade import TradeStatus
from traderos.domain.entities.value_objects import OHLCV
from traderos.domain.entities.value_objects import Timeframe
from traderos.domain.services.analysis_service import AnalysisService
from traderos.domain.services.paper_trading_service import PaperBrokerAdapter
from traderos.domain.services.portfolio_service import PortfolioService
from traderos.domain.services.reconciliation_service import PersistentKillSwitch
from traderos.domain.services.risk_service import KillSwitch
from traderos.domain.services.risk_service import RiskAssessment
from traderos.domain.services.risk_service import RiskService
from traderos.domain.services.risk_service import TradeVerdict
from traderos.domain.services.signal_service import SignalProvenance
from traderos.domain.services.strategy_framework import MarketState
from traderos.domain.services.strategy_framework import SignalResult
from traderos.domain.services.strategy_framework import StrategyBase
from traderos.domain.services.strategy_framework import registry as strategy_registry
from traderos.infrastructure.audit import AuditService as InMemoryAuditService
from traderos.infrastructure.events import InMemoryEventBus
from traderos.infrastructure.health import HealthService as InMemoryHealthService
from traderos.infrastructure.metrics import MetricsService as InMemoryMetricsService
from traderos.infrastructure.repositories.in_memory import InMemoryPositionRepository
from traderos.infrastructure.repositories.in_memory import InMemoryTradeRepository
from traderos.infrastructure.run_manifest import RunManifestService as InMemoryManifestService


def _provenance(direction: str = "long", confidence: float = 0.8) -> SignalProvenance:
    now = datetime.now(UTC)
    signal = Signal(
        market_id=uuid.uuid4(),
        strategy_id=uuid.uuid4(),
        direction=SignalDirection(direction),
        confidence=confidence,
        generated_at=now,
        expires_at=now + timedelta(hours=1),
    )
    return SignalProvenance(signal=signal, strategy_name="test", indicators_used={})


class _NoOrderIdBroker(BrokerAdapter):
    def place_market_order(self, market_id, side, quantity, close_price=None):
        price = close_price if close_price is not None else 100.0
        return FillResult(True, quantity, price, 0.0, "filled", "")

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


class _OrderedBroker(_NoOrderIdBroker):
    def place_market_order(self, market_id, side, quantity, close_price=None):
        price = close_price if close_price is not None else 100.0
        return FillResult(True, quantity, price, 0.0, "filled", "ord-1")


class _AlwaysLong(StrategyBase):
    name = "test_core_loop_always_long"

    def evaluate(self, state):
        return SignalResult("long", 0.8, {"reason": "test"})


def _make_executor(
    portfolio,
    risk,
    broker,
    *,
    analysis=None,
    signal_service=None,
    data_ingestion=None,
    metrics=None,
) -> CycleExecutor:
    if signal_service is None:
        ss = Mock()
        ss.process_evaluation.return_value = _provenance()
    else:
        ss = signal_service
    return CycleExecutor(
        mode=TradingMode.PAPER,
        signal_service=ss,
        risk_service=risk,
        portfolio_service=portfolio,
        execution=Mock(),
        analysis=analysis if analysis is not None else AnalysisService(),
        broker=broker,
        event_bus=InMemoryEventBus(),
        health=InMemoryHealthService(),
        audit=InMemoryAuditService(),
        metrics=metrics if metrics is not None else InMemoryMetricsService(),
        notifications=Mock(),
        run_manifest=InMemoryManifestService(),
        data_ingestion=data_ingestion,
        default_cash=10000.0,
    )


def _candles(close: str = "100.0", high: str = "105.0", low: str = "95.0", volume: str = "1000.0"):
    from decimal import Decimal

    return [
        Candle(
            market_id=uuid.uuid4(),
            ohlcv=OHLCV(
                Decimal(close),
                Decimal(high),
                Decimal(low),
                Decimal(close),
                Decimal(volume),
            ),
            timestamp=datetime.now(UTC),
            timeframe=Timeframe.MINUTE_1,
        )
    ] * 25


class TestCoreLoopInvariants:
    def _register(self, *clses) -> None:
        for cls in clses:
            strategy_registry._strategies[cls.name] = cls

    def _unregister(self, *clses) -> None:
        for cls in clses:
            strategy_registry._strategies.pop(cls.name, None)

    def test_fill_without_order_id_completes_and_creates_position(self) -> None:
        """D2: broker returns order_id='' (PaperBrokerAdapter behavior); fill must complete."""
        trade_repo = InMemoryTradeRepository()
        pos_repo = InMemoryPositionRepository()
        portfolio = PortfolioService(trade_repo, pos_repo)
        risk = RiskService()
        self._register(_AlwaysLong)
        try:
            executor = _make_executor(portfolio, risk, _NoOrderIdBroker())
            mid = uuid.uuid4()
            result = executor.run(mid, 100.0)
        finally:
            self._unregister(_AlwaysLong)

        assert result.errors == []
        assert result.trades == 1
        trades = trade_repo.list()
        assert len(trades) == 1
        assert trades[0].status == TradeStatus.FILLED
        assert trades[0].external_order_id == f"auto-{trades[0].id}"
        positions = pos_repo.list_open()
        assert len(positions) == 1
        assert positions[0].market_id == mid
        assert risk.kill_switch.consecutive_failures == 0

    def test_paper_broker_path_completes(self) -> None:
        """D2 end-to-end: the broker the factory actually configures."""
        trade_repo = InMemoryTradeRepository()
        pos_repo = InMemoryPositionRepository()
        portfolio = PortfolioService(trade_repo, pos_repo)
        risk = RiskService()
        self._register(_AlwaysLong)
        try:
            executor = _make_executor(portfolio, risk, PaperBrokerAdapter(fill_probability=1.0))
            result = executor.run(uuid.uuid4(), 100.0)
        finally:
            self._unregister(_AlwaysLong)

        assert result.errors == []
        assert result.trades == 1
        trades = trade_repo.list()
        assert trades[0].status == TradeStatus.FILLED
        positions = pos_repo.list_open()
        assert len(positions) == 1

    def test_pending_to_filled_transition_rejected(self) -> None:
        """I3: the raw state machine still rejects PENDING->FILLED (the D2 failure mode)."""
        trade = Trade(uuid.uuid4(), uuid.uuid4(), TradeSide.BUY, 10, 100.0)
        assert trade.status == TradeStatus.PENDING
        with pytest.raises(InvalidTradeTransitionError):
            trade.fill(10, 100.0)

    def test_fill_with_order_id_records_external_id_and_sizes_shares(self) -> None:
        """I1 + I5: order id recorded; quantity is shares (cash*alloc/price), not dollars."""
        trade_repo = InMemoryTradeRepository()
        pos_repo = InMemoryPositionRepository()
        portfolio = PortfolioService(trade_repo, pos_repo)
        risk = RiskService()
        self._register(_AlwaysLong)
        try:
            executor = _make_executor(portfolio, risk, _OrderedBroker())
            mid = uuid.uuid4()
            result = executor.run(mid, 50.0)
        finally:
            self._unregister(_AlwaysLong)

        assert result.errors == []
        assert result.trades == 1
        trades = trade_repo.list()
        assert trades[0].status == TradeStatus.FILLED
        assert trades[0].external_order_id == "ord-1"
        positions = pos_repo.list_open()
        assert len(positions) == 1
        allocation = min(0.02 * 0.8 * 10, 0.25)
        expected_shares = 10000.0 * allocation / 50.0
        assert positions[0].quantity == round(expected_shares, 8)
        assert positions[0].quantity != 10000.0 * allocation

    def test_close_position_feeds_realized_pnl_to_kill_switches(self) -> None:
        """I2/D4: closing a position reports realized PnL to both kill switches."""
        pks = PersistentKillSwitch()
        risk = RiskService(kill_switch=KillSwitch(), persistent_kill_switch=pks)
        portfolio = PortfolioService(
            InMemoryTradeRepository(), InMemoryPositionRepository(), risk_service=risk
        )
        mid = uuid.uuid4()
        pos = Position(
            market_id=mid, quantity=10.0, entry_price=100.0, current_price=100.0, pnl=0.0
        )
        portfolio.position_repo.add(pos)
        realized = portfolio.close_position(pos, 110.0)
        assert realized == 100.0
        assert risk.kill_switch.daily_realized_pnl == 100.0
        assert pks.state.daily_loss == 100.0

    def test_daily_loss_limit_trips_after_realized_loss(self) -> None:
        """D4: a configured daily loss limit is now enforceable via realized PnL."""
        risk = RiskService(kill_switch=KillSwitch(daily_loss_limit=100.0))
        portfolio = PortfolioService(
            InMemoryTradeRepository(), InMemoryPositionRepository(), risk_service=risk
        )
        mid = uuid.uuid4()
        pos = Position(
            market_id=mid, quantity=10.0, entry_price=100.0, current_price=100.0, pnl=0.0
        )
        portfolio.position_repo.add(pos)
        portfolio.close_position(pos, 90.0)
        verdict = risk.can_trade([])
        assert not verdict.allowed
        assert "loss limit" in verdict.reason.lower()

    def test_all_builtin_strategies_can_fire_on_cycle_indicator_set(self) -> None:
        """I6/D5: the cycle's indicator keys let every registered built-in strategy fire."""
        ts = datetime.now(UTC)
        cycle_keys = {
            "close": 140.0,
            "high": 145.0,
            "low": 135.0,
            "volume": 1000.0,
            "sma_20": 110.0,
            "sma_50": 100.0,
            "atr_14": 3.0,
            "bb_upper_20": 130.0,
            "bb_lower_20": 70.0,
        }
        state = MarketState(timestamp=ts, candles=[], indicators=cycle_keys)
        for name in ("moving_average_trend", "volatility_breakout", "mean_reversion"):
            cls = strategy_registry.get(name)
            assert cls is not None
            assert cls().evaluate(state) is not None, f"{name} should fire on cycle indicator set"

    def test_no_signal_on_fallback_indicators(self) -> None:
        """I9: with no candle data the fallback indicators must not fabricate signals."""
        ts = datetime.now(UTC)
        fallback = {
            "close": 100.0,
            "high": 101.0,
            "low": 99.0,
            "volume": 1000.0,
            "sma_20": 100.0,
            "sma_50": 100.0,
            "atr_14": 1.0,
            "bb_upper_20": 100.0,
            "bb_lower_20": 100.0,
        }
        state = MarketState(timestamp=ts, candles=[], indicators=fallback)
        for name in ("moving_average_trend", "volatility_breakout", "mean_reversion"):
            cls = strategy_registry.get(name)
            assert cls() is not None
            assert cls().evaluate(state) is None, f"{name} should NOT fire on fallback indicators"

    def test_cycle_metrics_are_per_cycle_and_duration_recorded(self) -> None:
        """I8/D6: cycles.completed once per cycle (not per strategy); duration gauge recorded."""

        class _AlsoLong(_AlwaysLong):
            name = "test_core_loop_also_long"

        trade_repo = InMemoryTradeRepository()
        pos_repo = InMemoryPositionRepository()
        portfolio = PortfolioService(trade_repo, pos_repo)
        risk = RiskService()
        metrics = InMemoryMetricsService()
        self._register(_AlwaysLong, _AlsoLong)
        try:
            executor = _make_executor(portfolio, risk, _OrderedBroker(), metrics=metrics)
            result = executor.run(uuid.uuid4(), 100.0)
            executor.run(uuid.uuid4(), 100.0)
        finally:
            self._unregister(_AlwaysLong, _AlsoLong)

        assert result.trades == 2
        assert metrics.get_counter("cycles.completed") == 2.0
        assert metrics.get_gauge("cycle.duration_ms") is not None

    def test_cycle_passes_real_atr_to_risk(self) -> None:
        """D9: assess_trade receives the computed ATR, not close_price*0.01."""
        data_ingestion = Mock()
        data_ingestion.fetch_candles.return_value = _candles(
            close="100.0", high="105.0", low="95.0"
        )

        calls: list[dict] = []
        risk = Mock()
        risk.can_trade.return_value = TradeVerdict(True, "")
        risk.kill_switch = KillSwitch()

        def _assess(**kwargs):
            calls.append(kwargs)
            return RiskAssessment(
                kelly_fraction=0.5,
                suggested_stop_loss=0.0,
                suggested_take_profit=0.0,
                risk_per_unit=0.0,
                max_risk_amount=0.0,
            )

        risk.assess_trade.side_effect = _assess

        portfolio = Mock()
        summary = Mock()
        summary.open_positions = []
        summary.total_equity = 10000.0
        portfolio.get_summary.return_value = summary
        portfolio.size_position.return_value = 1.0

        signal_service = Mock()
        signal_service.process_evaluation.return_value = _provenance()

        executor = _make_executor(
            portfolio,
            risk,
            _OrderedBroker(),
            signal_service=signal_service,
            data_ingestion=data_ingestion,
        )
        result = executor.run(uuid.uuid4(), 100.0)
        assert result.errors == []
        assert calls
        assert any(abs(c["atr"] - 10.0) < 0.001 for c in calls)

    def test_cycle_uses_real_candle_data_in_market_state(self) -> None:
        """I9: with candles available, high/low/volume and indicators come from the last candle."""
        captured: dict = {}

        class _Capture(StrategyBase):
            name = "test_core_loop_capture"

            def evaluate(self, state):
                captured.update(dict(state.indicators))

        data_ingestion = Mock()
        data_ingestion.fetch_candles.return_value = _candles(
            close="100.0", high="107.0", low="93.0", volume="2500.0"
        )
        portfolio = PortfolioService(InMemoryTradeRepository(), InMemoryPositionRepository())
        risk = RiskService()
        self._register(_Capture)
        try:
            executor = _make_executor(
                portfolio, risk, _OrderedBroker(), data_ingestion=data_ingestion
            )
            executor.run(uuid.uuid4(), 100.0)
        finally:
            self._unregister(_Capture)

        assert captured["high"] == 107.0
        assert captured["low"] == 93.0
        assert captured["volume"] == 2500.0
        assert captured["sma_20"] == 100.0
        assert "sma_50" in captured
        assert "bb_upper_20" in captured
        assert "bb_lower_20" in captured

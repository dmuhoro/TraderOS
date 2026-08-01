from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import UTC
from datetime import datetime

from traderos.application.models import CycleResult
from traderos.application.models import TradingMode
from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.entities.trade import TradeSide
from traderos.domain.exceptions import InfrastructureError
from traderos.domain.exceptions import ServiceError
from traderos.domain.ports import AuditPort
from traderos.domain.ports import Event
from traderos.domain.ports import EventBusPort
from traderos.domain.ports import HealthPort
from traderos.domain.ports import ManifestPort
from traderos.domain.ports import MetricsPort
from traderos.domain.services.analysis_service import AnalysisService
from traderos.domain.services.backtesting_service import BacktestingService
from traderos.domain.services.backtesting_service import synthetic_candles
from traderos.domain.services.breakout_detection import BreakoutDetectionService
from traderos.domain.services.data_ingestion_service import DataIngestionService
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.knowledge_graph_service import KnowledgeGraphService
from traderos.domain.services.notification_service import NotificationService
from traderos.domain.services.portfolio_service import PortfolioService
from traderos.domain.services.preflight_service import PreflightService
from traderos.domain.services.regime_detection import Regime
from traderos.domain.services.regime_detection import RegimeDetectionService
from traderos.domain.services.research_service import ResearchService
from traderos.domain.services.risk_service import RiskService
from traderos.domain.services.signal_service import SignalService
from traderos.domain.services.strategy_framework import MarketState
from traderos.domain.services.strategy_framework import registry as strategy_registry


class CycleExecutor:
    def __init__(
        self,
        mode: TradingMode,
        signal_service: SignalService,
        risk_service: RiskService,
        portfolio_service: PortfolioService,
        execution: ExecutionService,
        analysis: AnalysisService,
        broker: BrokerAdapter,
        event_bus: EventBusPort,
        health: HealthPort,
        audit: AuditPort,
        metrics: MetricsPort,
        notifications: NotificationService,
        run_manifest: ManifestPort,
        data_ingestion: DataIngestionService | None = None,
        default_cash: float = 10000.0,
        preflight_service: PreflightService | None = None,
        enabled_strategies: Callable[[], list[tuple[str, str, dict]]] | None = None,
        backtest: BacktestingService | None = None,
        knowledge_graph: KnowledgeGraphService | None = None,
        research: ResearchService | None = None,
    ) -> None:
        self._mode = mode
        self._signal_service = signal_service
        self._risk_service = risk_service
        self._portfolio_service = portfolio_service
        self._execution = execution
        self._analysis = analysis
        self._broker = broker
        self._event_bus = event_bus
        self._health = health
        self._audit = audit
        self._metrics = metrics
        self._notifications = notifications
        self._run_manifest = run_manifest
        self._data_ingestion = data_ingestion
        self._default_cash = default_cash
        self._preflight_service = preflight_service
        self._enabled_strategies = enabled_strategies
        self._backtest = backtest
        self._knowledge_graph = knowledge_graph
        self._research = research

    def run(
        self, market_id: uuid.UUID, close_price: float, candle_time: datetime | None = None
    ) -> CycleResult:
        start = time.perf_counter()
        errors: list[str] = []
        signals_count = 0
        trades_count = 0

        cycle_id = uuid.uuid4()
        self._event_bus.publish(
            Event(
                "cycle.start",
                {
                    "cycle_id": str(cycle_id),
                    "market_id": str(market_id),
                },
            )
        )

        try:
            if self._mode == TradingMode.BACKTEST:
                return self._run_backtest_cycle(
                    market_id=market_id,
                    cycle_id=cycle_id,
                    start=start,
                    errors=errors,
                    candle_time=candle_time,
                )

            candles: list = []
            if self._data_ingestion is not None:
                candles = self._data_ingestion.fetch_candles(market_id, limit=100)

            sma_20 = close_price
            sma_50 = close_price
            atr_14 = close_price * 0.01
            bb_upper_20 = close_price
            bb_lower_20 = close_price
            high = close_price * 1.01
            low = close_price * 0.99
            volume = 1000.0
            if candles:
                last_candle = candles[-1]
                high = float(last_candle.ohlcv.high)
                low = float(last_candle.ohlcv.low)
                volume = float(last_candle.ohlcv.volume)
                sma_20_results = self._analysis.compute_sma(candles, 20)
                if sma_20_results:
                    sma_20 = sma_20_results[-1].value
                sma_50_results = self._analysis.compute_sma(candles, 50)
                if sma_50_results:
                    sma_50 = sma_50_results[-1].value
                atr_results = self._analysis.compute_atr(candles, 14)
                if atr_results:
                    atr_14 = atr_results[-1].value
                bb = self._analysis.compute_bollinger_bands(candles, 20, 2.0)
                if bb.upper:
                    bb_upper_20 = bb.upper[-1].value
                if bb.lower:
                    bb_lower_20 = bb.lower[-1].value

            regime_value = Regime.UNKNOWN.value
            if candles:
                regime_results = RegimeDetectionService.detect(candles)
                if regime_results:
                    regime_value = regime_results[-1].regime.value
                breakout_events = [
                    {
                        "timestamp": e.timestamp.isoformat(),
                        "type": e.event_type,
                        "description": e.description,
                    }
                    for e in BreakoutDetectionService.analyze(candles)
                ]
                self._event_bus.publish(
                    Event(
                        "cycle.analysis",
                        {
                            "market_id": str(market_id),
                            "regime": regime_value,
                            "breakout_events": breakout_events,
                        },
                    )
                )

            strategy_sources = (
                self._enabled_strategies()
                if self._enabled_strategies is not None
                else [(name, name, {}) for name in strategy_registry.list()]
            )
            for name, template, params in strategy_sources:
                try:
                    strat_cls = strategy_registry.get(template)
                    if strat_cls is None:
                        continue
                    strategy = strat_cls(params=params)
                    state = MarketState(
                        candles=candles,
                        indicators={
                            "close": close_price,
                            "high": high,
                            "low": low,
                            "volume": volume,
                            "sma_20": sma_20,
                            "sma_50": sma_50,
                            "atr_14": atr_14,
                            "bb_upper_20": bb_upper_20,
                            "bb_lower_20": bb_lower_20,
                        },
                        timestamp=candle_time or datetime.now(UTC),
                    )
                    result = strategy.evaluate(state)
                    if result is None:
                        continue

                    provenance = self._signal_service.process_evaluation(
                        market_id=market_id,
                        strategy_id=uuid.uuid5(uuid.NAMESPACE_DNS, name),
                        strategy_name=name,
                        result=result,
                        indicators=state.indicators,
                    )
                    if provenance is None:
                        continue
                    signals_count += 1
                    self._event_bus.publish(
                        Event(
                            "signal.generated",
                            {
                                "market_id": str(market_id),
                                "strategy": name,
                                "direction": result.direction,
                                "confidence": result.confidence,
                            },
                        )
                    )

                    for signal in [provenance.signal]:
                        if self._preflight_service is not None:
                            pf = self._preflight_service.check(
                                live_mode=self._mode == TradingMode.LIVE
                            )
                            if not pf.passed:
                                for f in pf.failures:
                                    errors.append(f"{name}: preflight: {f}")
                                continue
                        positions = self._portfolio_service.get_summary(0).open_positions
                        verdict = self._risk_service.can_trade(positions)
                        if not verdict.allowed:
                            errors.append(f"{name}: {verdict.reason}")
                            continue
                        cash = self._cash_balance()
                        eq = self._portfolio_service.get_summary(cash).total_equity
                        risk = self._risk_service.assess_trade(
                            price=close_price,
                            confidence=signal.confidence,
                            atr=atr_14,
                            account_equity=eq,
                        )
                        if risk.kelly_fraction <= 0:
                            continue
                        qty = self._portfolio_service.size_position(
                            cash=cash,
                            confidence=signal.confidence,
                            price=close_price,
                        )
                        if qty <= 0:
                            continue
                        side = "buy" if signal.direction.value == "long" else "sell"

                        if self._preflight_service is not None:
                            pf = self._preflight_service.check(
                                live_mode=self._mode == TradingMode.LIVE
                            )
                            if not pf.passed:
                                for f in pf.failures:
                                    errors.append(f"{name}: preflight (re-check): {f}")
                                continue

                        fill = self._broker.place_market_order(
                            market_id, side, qty, close_price=close_price
                        )
                        if fill.filled:
                            trade = self._portfolio_service.open_trade(
                                signal_id=signal.id,
                                market_id=market_id,
                                side=(
                                    TradeSide.BUY
                                    if signal.direction.value == "long"
                                    else TradeSide.SELL
                                ),
                                quantity=fill.fill_quantity,
                                price=fill.fill_price,
                            )
                            if fill.order_id:
                                trade.submit(str(fill.order_id))
                                self._portfolio_service.update_trade(trade)
                            self._portfolio_service.fill_trade(trade, fill_price=fill.fill_price)
                            trades_count += 1
                            self._event_bus.publish(
                                Event(
                                    "trade.executed",
                                    {
                                        "market_id": str(market_id),
                                        "side": side,
                                        "qty": fill.fill_quantity,
                                        "price": fill.fill_price,
                                        "trade_id": str(trade.id),
                                        "order_id": str(trade.external_order_id or ""),
                                    },
                                )
                            )
                            self._risk_service.kill_switch.record_success()
                            self._metrics.counter("trades.executed")
                            self._record_trade_evidence(
                                market_id=market_id,
                                strategy_name=name,
                                side=side,
                                quantity=fill.fill_quantity,
                                price=fill.fill_price,
                            )
                        else:
                            self._risk_service.kill_switch.record_failure()
                except (ValueError, RuntimeError, OSError, ServiceError, InfrastructureError) as e:
                    errors.append(f"{name}: {e}")
                    self._event_bus.publish(
                        Event(
                            "cycle.error",
                            {
                                "market_id": str(market_id),
                                "strategy": name,
                                "error": str(e),
                            },
                        )
                    )

            self._health.report_healthy(f"market.{market_id}")
        except (ValueError, RuntimeError, OSError, ServiceError, InfrastructureError) as e:
            errors.append(str(e))
            self._health.report_unhealthy(f"market.{market_id}", str(e))

        self._metrics.counter("cycles.completed")
        duration = (time.perf_counter() - start) * 1000
        self._metrics.gauge("cycle.duration_ms", duration)

        t = candle_time or datetime.now(UTC)
        self._event_bus.publish(
            Event(
                "cycle.complete",
                {
                    "cycle_id": str(cycle_id),
                    "market_id": str(market_id),
                    "signals": signals_count,
                    "trades": trades_count,
                    "errors": errors,
                    "duration_ms": round(duration, 2),
                },
            )
        )

        return CycleResult(market_id, signals_count, trades_count, errors, duration, t)

    def _cash_balance(self) -> float:
        if self._mode == TradingMode.LIVE:
            return self._broker.get_account_balance()
        return self._default_cash

    def _record_trade_evidence(
        self,
        market_id: uuid.UUID,
        strategy_name: str,
        side: str,
        quantity: float,
        price: float,
    ) -> None:
        if self._knowledge_graph is not None:
            market_label = f"market:{market_id}"
            market_nodes = self._knowledge_graph.nodes.get_by_label(market_label)
            market_node = (
                market_nodes[0]
                if market_nodes
                else self._knowledge_graph.add_node(
                    label=market_label,
                    node_type="market",
                    content=f"Traded market {market_id}",
                )
            )
            strategy_node = self._knowledge_graph.add_node(
                label=f"strategy:{strategy_name}",
                node_type="strategy",
                content=f"{strategy_name} fired on {market_id}",
            )
            self._knowledge_graph.add_edge(strategy_node.id, market_node.id, "trades_in")
            self._knowledge_graph.add_edge(market_node.id, strategy_node.id, "has_strategy")
        if self._research is not None:
            self._research.create_observation(
                symbol=str(market_id),
                content=f"{strategy_name} {side} qty={quantity} @ {price}",
                tags=[strategy_name, "trade"],
            )

    def _run_backtest_cycle(
        self,
        market_id: uuid.UUID,
        cycle_id: uuid.UUID,
        start: float,
        errors: list[str],
        candle_time: datetime | None,
    ) -> CycleResult:
        if self._backtest is None:
            raise ServiceError("BacktestingService is not available in BACKTEST mode")

        candles: list = []
        if self._data_ingestion is not None:
            candles = self._data_ingestion.fetch_candles(market_id, limit=200)
        if not candles:
            candles = synthetic_candles(count=50, market_id=market_id)

        signals_count = 0
        trades_count = 0

        strategy_sources = (
            self._enabled_strategies()
            if self._enabled_strategies is not None
            else [(name, name, {}) for name in strategy_registry.list()]
        )
        for name, template, params in strategy_sources:
            try:
                strat_cls = strategy_registry.get(template)
                if strat_cls is None:
                    continue
                strategy = strat_cls(params=params)
                result, steps = self._backtest.run(strategy, candles, market_id)
                signals = len([s for s in steps if s.order is not None])
                trades = len([s for s in steps if s.fill_price is not None])
                signals_count += signals
                trades_count += trades
                metrics = result.metrics
                self._run_manifest.record(
                    "orchestrator",
                    "backtest",
                    metadata={
                        "market": str(market_id),
                        "strategy": name,
                        "total_return": metrics.total_return,
                        "sharpe_ratio": metrics.sharpe_ratio,
                        "max_drawdown": metrics.max_drawdown,
                    },
                )
                self._event_bus.publish(
                    Event(
                        "backtest.complete",
                        {
                            "market_id": str(market_id),
                            "strategy": name,
                            "signals": signals,
                            "trades": trades,
                            "total_return": metrics.total_return,
                            "sharpe_ratio": metrics.sharpe_ratio,
                            "max_drawdown": metrics.max_drawdown,
                        },
                    )
                )
            except (ValueError, RuntimeError, OSError, ServiceError, InfrastructureError) as e:
                errors.append(f"{name}: backtest: {e}")
                self._event_bus.publish(
                    Event(
                        "cycle.error",
                        {
                            "market_id": str(market_id),
                            "strategy": name,
                            "error": str(e),
                        },
                    )
                )

        self._metrics.counter("cycles.completed")
        self._metrics.counter("backtests.completed")
        duration = (time.perf_counter() - start) * 1000
        self._metrics.gauge("cycle.duration_ms", duration)

        t = candle_time or datetime.now(UTC)
        self._event_bus.publish(
            Event(
                "cycle.complete",
                {
                    "cycle_id": str(cycle_id),
                    "market_id": str(market_id),
                    "signals": signals_count,
                    "trades": trades_count,
                    "errors": errors,
                    "duration_ms": round(duration, 2),
                },
            )
        )
        return CycleResult(market_id, signals_count, trades_count, errors, duration, t)

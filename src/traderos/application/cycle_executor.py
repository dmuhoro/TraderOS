from __future__ import annotations

import time
import uuid
from datetime import UTC
from datetime import datetime

from traderos.application.models import CycleResult
from traderos.application.models import TradingMode
from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.entities.trade import TradeSide
from traderos.domain.ports import AuditPort
from traderos.domain.ports import Event
from traderos.domain.ports import EventBusPort
from traderos.domain.ports import HealthPort
from traderos.domain.ports import ManifestPort
from traderos.domain.ports import MetricsPort
from traderos.domain.services.analysis_service import AnalysisService
from traderos.domain.services.data_ingestion_service import DataIngestionService
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.notification_service import NotificationService
from traderos.domain.services.portfolio_service import PortfolioService
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
                self._run_manifest.record(
                    "orchestrator", "cycle", metadata={"market": str(market_id), "mode": "backtest"}
                )
                return CycleResult(market_id, 0, 0, [], 0.0, datetime.now(UTC))

            candles: list = []
            if self._data_ingestion is not None:
                candles = self._data_ingestion.fetch_candles(market_id, limit=100)
            sma_20 = close_price
            atr_14 = close_price * 0.01
            if candles:
                sma_results = self._analysis.compute_sma(candles, 20)
                if sma_results:
                    sma_20 = sma_results[-1].value
                atr_results = self._analysis.compute_atr(candles, 14)
                if atr_results:
                    atr_14 = atr_results[-1].value

            strategies = strategy_registry.list()
            for name in strategies:
                try:
                    strat_cls = strategy_registry.get(name)
                    if strat_cls is None:
                        continue
                    strategy = strat_cls()
                    state = MarketState(
                        candles=candles,
                        indicators={
                            "close": close_price,
                            "high": close_price * 1.01,
                            "low": close_price * 0.99,
                            "volume": 1000.0,
                            "sma_20": sma_20,
                            "atr_14": atr_14,
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
                            atr=close_price * 0.01,
                            account_equity=eq,
                        )
                        if risk.kelly_fraction <= 0:
                            continue
                        qty = self._portfolio_service.size_position(
                            cash=cash,
                            confidence=signal.confidence,
                        )
                        if qty <= 0:
                            continue
                        side = "buy" if signal.direction.value == "long" else "sell"
                        fill = self._broker.place_market_order(
                            market_id, side, qty, close_price=close_price
                        )
                        if fill.filled:
                            self._portfolio_service.open_trade(
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
                            trades_count += 1
                            self._event_bus.publish(
                                Event(
                                    "trade.executed",
                                    {
                                        "market_id": str(market_id),
                                        "side": side,
                                        "qty": fill.fill_quantity,
                                        "price": fill.fill_price,
                                    },
                                )
                            )
                            self._risk_service.kill_switch.record_success()
                            self._metrics.counter("trades.executed")
                        else:
                            self._risk_service.kill_switch.record_failure()
                except (ValueError, RuntimeError, OSError) as e:
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
                finally:
                    self._metrics.counter("cycles.completed")

            self._health.report_healthy(f"market.{market_id}")
        except (ValueError, RuntimeError, OSError) as e:
            errors.append(str(e))
            self._health.report_unhealthy(f"market.{market_id}", str(e))

        duration = (time.perf_counter() - start) * 1000
        self._metrics.timing("cycle.duration_ms").stop()

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

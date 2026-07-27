from __future__ import annotations

import signal
import time
import uuid
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from enum import Enum
from typing import Any

from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.entities.trade import TradeSide
from traderos.domain.services.analysis_service import AnalysisService
from traderos.domain.services.backtesting_service import BacktestingService
from traderos.domain.services.data_ingestion_service import DataIngestionService
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.notification_service import NotificationService
from traderos.domain.services.paper_trading_service import PaperTradingService
from traderos.domain.services.portfolio_service import PortfolioService
from traderos.domain.services.risk_service import RiskService
from traderos.domain.services.signal_service import SignalService
from traderos.domain.services.strategy_framework import MarketState
from traderos.domain.services.strategy_framework import registry as strategy_registry
from traderos.infrastructure.audit import AuditService
from traderos.infrastructure.events import Event
from traderos.infrastructure.events import EventBus
from traderos.infrastructure.health import HealthService
from traderos.infrastructure.metrics import MetricsService
from traderos.infrastructure.run_manifest import RunManifestService


class TradingMode(Enum):
    PAPER = "paper"
    LIVE = "live"
    BACKTEST = "backtest"


@dataclass
class CycleResult:
    market_id: uuid.UUID
    signals: int
    trades: int
    errors: list[str]
    duration_ms: float
    timestamp: datetime


@dataclass
class TradingOrchestrator:
    mode: TradingMode
    signal_service: SignalService
    risk_service: RiskService
    portfolio_service: PortfolioService
    execution: ExecutionService
    analysis: AnalysisService
    broker: BrokerAdapter
    backtest: BacktestingService
    paper: PaperTradingService | None
    event_bus: EventBus
    health: HealthService
    audit: AuditService
    metrics: MetricsService
    notifications: NotificationService
    run_manifest: RunManifestService
    data_ingestion: DataIngestionService | None = None

    default_cash: float = 10000.0
    market_ids: list[uuid.UUID] = field(default_factory=list)
    _running: bool = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        self._running = True
        self.health.report_healthy("orchestrator", "started")
        self.audit.record("orchestrator.start", "system", "orchestrator", f"mode={self.mode.value}")
        self.notifications.info("Orchestrator Started", f"Trading mode: {self.mode.value}")
        self.run_manifest.record("orchestrator", "start", metadata={"mode": self.mode.value})

    def _cash_balance(self) -> float:
        if self.mode == TradingMode.LIVE:
            return self.broker.get_account_balance()
        return self.default_cash

    def stop(self) -> None:
        self._running = False
        self.health.report_healthy("orchestrator", "stopped")
        self.audit.record("orchestrator.stop", "system", "orchestrator")
        self.notifications.info("Orchestrator Stopped")
        self.run_manifest.record("orchestrator", "stop")

    def run_cycle(
        self, market_id: uuid.UUID, close_price: float, candle_time: datetime | None = None
    ) -> CycleResult:
        start = time.perf_counter()
        errors: list[str] = []
        signals_count = 0
        trades_count = 0

        cycle_id = uuid.uuid4()
        self.event_bus.publish(
            Event(
                "cycle.start",
                {
                    "cycle_id": str(cycle_id),
                    "market_id": str(market_id),
                },
            )
        )

        try:
            if self.mode == TradingMode.BACKTEST:
                self.run_manifest.record(
                    "orchestrator", "cycle", metadata={"market": str(market_id), "mode": "backtest"}
                )
                return CycleResult(market_id, 0, 0, [], 0.0, datetime.now(UTC))

            candles: list = []
            if self.data_ingestion is not None:
                candles = self.data_ingestion.fetch_candles(market_id, limit=100)
            sma_20 = close_price
            atr_14 = close_price * 0.01
            if candles:
                sma_results = self.analysis.compute_sma(candles, 20)
                if sma_results:
                    sma_20 = sma_results[-1].value
                atr_results = self.analysis.compute_atr(candles, 14)
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

                    provenance = self.signal_service.process_evaluation(
                        market_id=market_id,
                        strategy_id=uuid.uuid5(uuid.NAMESPACE_DNS, name),
                        strategy_name=name,
                        result=result,
                        indicators=state.indicators,
                    )
                    if provenance is None:
                        continue
                    signals_count += 1
                    self.event_bus.publish(
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
                        cash = self._cash_balance()
                        risk = self.risk_service.assess_trade(
                            price=close_price,
                            confidence=signal.confidence,
                            atr=close_price * 0.01,
                            account_equity=self.portfolio_service.get_summary(cash).total_equity,
                        )
                        if risk.kelly_fraction <= 0:
                            continue
                        qty = self.portfolio_service.size_position(
                            cash=cash,
                            confidence=signal.confidence,
                        )
                        if qty <= 0:
                            continue
                        side = "buy" if signal.direction.value == "long" else "sell"
                        fill = self.broker.place_market_order(
                            market_id, side, qty, close_price=close_price
                        )
                        if fill.filled:
                            self.portfolio_service.open_trade(
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
                            self.event_bus.publish(
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
                            self.metrics.counter("trades.executed")
                except (ValueError, RuntimeError, OSError) as e:
                    errors.append(f"{name}: {e}")
                    self.event_bus.publish(
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
                    self.metrics.counter("cycles.completed")

            self.health.report_healthy(f"market.{market_id}")
        except (ValueError, RuntimeError, OSError) as e:
            errors.append(str(e))
            self.health.report_unhealthy(f"market.{market_id}", str(e))

        duration = (time.perf_counter() - start) * 1000
        self.metrics.timing("cycle.duration_ms").stop()

        t = candle_time or datetime.now(UTC)
        self.event_bus.publish(
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

    def run_forever(self, interval_seconds: int = 60) -> None:
        self.start()

        def handle_stop(signum: int, frame: object | None = None) -> None:
            self.stop()

        signal.signal(signal.SIGINT, handle_stop)
        signal.signal(signal.SIGTERM, handle_stop)

        while self._running:
            for mid in self.market_ids:
                if not self._running:
                    break
                try:
                    close_price = 100.0
                    if self.data_ingestion is not None:
                        price = self.data_ingestion.get_latest_close(mid)
                        if price is not None:
                            close_price = price
                    result = self.run_cycle(mid, close_price)
                    if result.errors:
                        for err in result.errors:
                            self.notifications.warning("Cycle Error", f"{mid}: {err}")
                except (ValueError, RuntimeError, OSError) as e:
                    self.notifications.warning("Cycle Panic", f"{mid}: {e}")
                    self.health.report_unhealthy(f"market.{mid}", str(e))
            time.sleep(interval_seconds)

    def get_status(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "running": self._running,
            "markets": len(self.market_ids),
            "health": self.health.summary(),
            "metrics": self.metrics.snapshot(),
        }

from __future__ import annotations

import uuid
from typing import Any

from traderos.application.orchestrator import TradingMode
from traderos.application.orchestrator import TradingOrchestrator
from traderos.domain.services.backtesting_service import BacktestingService
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.paper_trading_service import PaperBrokerAdapter
from traderos.domain.services.paper_trading_service import PaperTradingService
from traderos.domain.services.portfolio_service import PortfolioService
from traderos.domain.services.risk_service import RiskService
from traderos.domain.services.signal_service import SignalService
from traderos.domain.services.strategy_framework import registry as strategy_registry
from traderos.infrastructure.audit import AuditService
from traderos.infrastructure.events import InMemoryEventBus
from traderos.infrastructure.health import HealthService
from traderos.infrastructure.metrics import MetricsService
from traderos.infrastructure.run_manifest import RunManifestService
from datetime import UTC

try:
    from fastapi import FastAPI, HTTPException
    from fastapi import Query
    from pydantic import BaseModel
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False


class TradeRequest(BaseModel):
    market_id: str
    side: str
    quantity: float


class BacktestRequest(BaseModel):
    strategy: str
    candles: int = 50


class PaperSessionResponse(BaseModel):
    id: str
    status: str
    capital: float


_orchestrator: TradingOrchestrator | None = None


def create_orchestrator(mode: str = "paper") -> TradingOrchestrator:
    global _orchestrator
    if _orchestrator is not None:
        return _orchestrator

    paper = PaperTradingService(
        broker=PaperBrokerAdapter(fill_probability=1.0),
        signal_service=SignalService.__new__(SignalService),
        risk_service=RiskService(),
        portfolio_service=PortfolioService.__new__(PortfolioService),
        execution=ExecutionService(),
    )

    orch = TradingOrchestrator(
        mode=TradingMode(mode),
        signal_service=SignalService.__new__(SignalService),
        risk_service=RiskService(),
        portfolio_service=PortfolioService.__new__(PortfolioService),
        execution=ExecutionService(),
        analysis=None,  # type: ignore
        broker=PaperBrokerAdapter(fill_probability=1.0),
        backtest=BacktestingService(execution=ExecutionService()),
        paper=paper,
        event_bus=InMemoryEventBus(),
        health=HealthService(),
        audit=AuditService(),
        metrics=MetricsService(),
        notifications=None,  # type: ignore
        run_manifest=RunManifestService(),
        market_ids=[],
    )
    _orchestrator = orch
    return orch


def _ensure_fastapi() -> None:
    if not HAS_FASTAPI:
        raise ImportError(
            "FastAPI is required. Install with: pip install 'traderos[api]'"
        )


def build_app() -> Any:
    _ensure_fastapi()
    app = FastAPI(title="TraderOS API", version="0.3.0")

    @app.get("/health")
    def get_health():
        orch = create_orchestrator()
        return {"status": "ok", "mode": orch.mode.value, "running": orch._running}

    @app.get("/strategies")
    def list_strategies():
        return {"strategies": strategy_registry.list()}

    @app.get("/strategies/{name}")
    def get_strategy(name: str):
        strat = strategy_registry.get(name)
        if strat is None:
            raise HTTPException(404, f"Strategy '{name}' not found")
        return {"name": name, "version": strat.version}

    @app.post("/backtest")
    def run_backtest(req: BacktestRequest):
        strat_cls = strategy_registry.get(req.strategy)
        if strat_cls is None:
            raise HTTPException(404, f"Strategy '{req.strategy}' not found")
        strategy = strat_cls()
        svc = BacktestingService(execution=ExecutionService())
        from datetime import datetime
        from decimal import Decimal
        from traderos.domain.entities import Candle, OHLCV, Timeframe
        mid = uuid.uuid4()
        candles = [
            Candle(
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
            for i in range(req.candles)
        ]
        result, _ = svc.run(strategy, candles, mid)
        m = result.metrics
        return {
            "total_return": m.total_return,
            "sharpe_ratio": m.sharpe_ratio,
            "max_drawdown": m.max_drawdown,
            "win_rate": m.win_rate,
            "sortino_ratio": m.sortino_ratio,
            "calmar_ratio": m.calmar_ratio,
        }

    @app.post("/orchestrator/start")
    def start_orchestrator():
        orch = create_orchestrator()
        orch.start()
        return {"status": "started", "mode": orch.mode.value}

    @app.post("/orchestrator/stop")
    def stop_orchestrator():
        orch = create_orchestrator()
        orch.stop()
        return {"status": "stopped"}

    @app.get("/orchestrator/status")
    def orchestrator_status():
        orch = create_orchestrator()
        return orch.get_status()

    @app.post("/papertrade/session")
    def create_paper_session():
        orch = create_orchestrator()
        if orch.paper is None:
            raise HTTPException(400, "Paper trading not configured")
        session = orch.paper.create_session(uuid.uuid4(), [])
        return PaperSessionResponse(
            id=str(session.id),
            status=session.status.value,
            capital=session.current_capital,
        )

    @app.get("/papertrade/sessions")
    def list_paper_sessions():
        orch = create_orchestrator()
        if orch.paper is None:
            return {"sessions": []}
        return {"sessions": [
            {"id": str(s.id), "status": s.status.value, "capital": s.current_capital}
            for s in orch.paper.list_sessions()
        ]}

    @app.get("/audit")
    def get_audit(limit: int = Query(10, ge=1, le=100)):
        orch = create_orchestrator()
        return {"entries": [
            {"action": e.action, "actor": e.actor, "resource": e.resource,
             "timestamp": e.timestamp.isoformat()}
            for e in orch.audit.get_entries(limit=limit)
        ]}

    @app.get("/metrics")
    def get_metrics():
        orch = create_orchestrator()
        return {"metrics": orch.metrics.snapshot()}

    @app.get("/manifest")
    def get_manifest(service: str | None = None):
        orch = create_orchestrator()
        return {"runs": [
            {"service": e.service, "action": e.action, "status": e.status,
             "duration_ms": e.duration_ms}
            for e in orch.run_manifest.get_runs(service=service)
        ]}

    return app

# pyright: reportUntypedFunctionDecorator=false, reportUnusedFunction=false, reportOptionalCall=false, reportPrivateUsage=false

from __future__ import annotations

import uuid
from datetime import UTC
from importlib.metadata import version
from typing import TYPE_CHECKING
from typing import Any

from traderos.application.factory import build_orchestrator
from traderos.application.orchestrator import TradingOrchestrator
from traderos.domain.services.backtesting_service import BacktestingService
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.strategy_framework import registry as strategy_registry
from traderos.infrastructure.config.config_loader import Config

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi import HTTPException
    from fastapi import Query
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel

    _has_fastapi = True
else:
    try:
        from fastapi import FastAPI
        from fastapi import HTTPException
        from fastapi import Query
        from fastapi.middleware.cors import CORSMiddleware
        from pydantic import BaseModel  # type: ignore[assignment]

        _has_fastapi = True
    except ImportError:
        _has_fastapi = False
        BaseModel = object  # type: ignore[assignment]
        FastAPI = None  # type: ignore[assignment]
        HTTPException = None  # type: ignore[assignment]
        Query = None  # type: ignore[assignment]
        CORSMiddleware = None  # type: ignore[assignment]


class TradeRequest(BaseModel):  # type: ignore[valid-type,misc]
    market_id: str
    side: str
    quantity: float


class BacktestRequest(BaseModel):  # type: ignore[valid-type,misc]
    strategy: str
    candles: int = 50


class CreatePaperSessionRequest(BaseModel):  # type: ignore[valid-type,misc]
    market_ids: list[str] | None = None


class PaperSessionResponse(BaseModel):  # type: ignore[valid-type,misc]
    id: str
    status: str
    capital: float


_orch_cache: dict[str, TradingOrchestrator] = {}


def create_orchestrator(mode: str = "paper") -> TradingOrchestrator:
    if mode in _orch_cache:
        return _orch_cache[mode]

    cfg = Config.load()
    orch = build_orchestrator(mode=mode, config=cfg)
    _orch_cache[mode] = orch
    return orch


def reset_orchestrator(mode: str | None = None) -> None:
    if mode:
        _orch_cache.pop(mode, None)
    else:
        _orch_cache.clear()


def ensure_fastapi() -> None:
    if not _has_fastapi:
        raise ImportError("FastAPI is required. Install with: pip install 'traderos[api]'")


def build_app() -> Any:
    ensure_fastapi()
    app = FastAPI(title="TraderOS API", version=version("traderos"))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def get_health():
        orch = create_orchestrator()
        return {"status": "ok", "mode": orch.mode.value, "running": orch.running}

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

        from traderos.domain.entities import OHLCV
        from traderos.domain.entities import Candle
        from traderos.domain.entities import Timeframe

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
    def create_paper_session(req: CreatePaperSessionRequest | None = None):
        orch = create_orchestrator()
        if orch.paper is None:
            raise HTTPException(400, "Paper trading not configured")
        cfg = Config.load()
        symbols: list[str] = cfg.get("data_collection.forex_symbols", []) or []
        symbols += cfg.get("data_collection.crypto_symbols", []) or []
        mids = [uuid.uuid5(uuid.NAMESPACE_DNS, s) for s in symbols]
        if req is not None and req.market_ids:
            mids = [uuid.UUID(m) for m in req.market_ids]
        if not mids:
            mids = [uuid.uuid4()]
        session = orch.paper.create_session(uuid.uuid4(), mids)
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
        return {
            "sessions": [
                {"id": str(s.id), "status": s.status.value, "capital": s.current_capital}
                for s in orch.paper.list_sessions()
            ]
        }

    @app.get("/audit")
    def get_audit(limit: int = Query(10, ge=1, le=100)):
        orch = create_orchestrator()
        return {
            "entries": [
                {
                    "action": e.action,
                    "actor": e.actor,
                    "resource": e.resource,
                    "timestamp": e.timestamp.isoformat(),
                }
                for e in orch.audit.get_entries(limit=limit)
            ]
        }

    @app.get("/metrics")
    def get_metrics():
        orch = create_orchestrator()
        if not orch.running:
            return {"metrics": {}, "warning": "Orchestrator not running"}
        return {"metrics": orch.metrics.snapshot()}

    @app.get("/manifest")
    def get_manifest(service: str | None = None):
        orch = create_orchestrator()
        return {
            "runs": [
                {
                    "service": e.service,
                    "action": e.action,
                    "status": e.status,
                    "duration_ms": e.duration_ms,
                }
                for e in orch.run_manifest.get_runs(service=service)
            ]
        }

    return app

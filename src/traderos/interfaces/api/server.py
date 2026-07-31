# pyright: reportUntypedFunctionDecorator=false, reportUnusedFunction=false, reportOptionalCall=false, reportPrivateUsage=false

from __future__ import annotations

import logging
import os
import time
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
from traderos.infrastructure.health import run_with_timeout
from traderos.infrastructure.logging import setup_json_logging
from traderos.infrastructure.monitoring import PrometheusMetricsService
from traderos.infrastructure.rate_limiter import RateLimiter

if TYPE_CHECKING:
    from fastapi import APIRouter
    from fastapi import FastAPI
    from fastapi import HTTPException
    from fastapi import Query
    from fastapi import Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import Response
    from pydantic import BaseModel

    _has_fastapi = True
else:
    try:
        from fastapi import APIRouter
        from fastapi import FastAPI
        from fastapi import HTTPException
        from fastapi import Query
        from fastapi import Request
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import Response
        from pydantic import BaseModel  # type: ignore[assignment]

        _has_fastapi = True
    except ImportError:
        _has_fastapi = False
        APIRouter = None  # type: ignore[assignment]
        BaseModel = object  # type: ignore[assignment]
        FastAPI = None  # type: ignore[assignment]
        HTTPException = None  # type: ignore[assignment]
        Query = None  # type: ignore[assignment]
        Request = None  # type: ignore[assignment]
        CORSMiddleware = None  # type: ignore[assignment]
        Response = type("Response", (), {})  # type: ignore[assignment]


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
_api_key: str | None = None
_metrics_service = PrometheusMetricsService()
_rate_limiter = RateLimiter(
    max_requests=int(os.getenv("RATE_LIMIT_MAX", "100")), window_seconds=60.0
)
ORCHESTRATOR_READY_TIMEOUT = float(os.getenv("ORCHESTRATOR_READY_TIMEOUT", "5.0"))


def _load_api_key() -> str | None:
    global _api_key
    if _api_key is None:
        _api_key = os.getenv("TRADEROS_API_KEY") or None
    return _api_key


def _verify_api_key(request: Request) -> None:
    key = _load_api_key()
    if key is None:
        return
    header_key = request.headers.get("X-API-Key")
    if header_key != key:
        raise HTTPException(401, "Unauthorized: invalid or missing API key")


def create_orchestrator(
    mode: str = "paper", *, timeout: float | None = None
) -> TradingOrchestrator:
    if mode in _orch_cache:
        return _orch_cache[mode]

    cfg = Config.load()
    if timeout is not None:
        orch = run_with_timeout(lambda: build_orchestrator(mode=mode, config=cfg), timeout)
    else:
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


def _error_response(status_code: int, message: str):
    from starlette.responses import JSONResponse

    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": status_code, "message": message}},
    )


def _prometheus_metrics() -> Response | None:
    try:
        from prometheus_client import generate_latest

        return Response(
            content=generate_latest(_metrics_service.registry),
            media_type="text/plain; version=0.0.4",
        )
    except ImportError:
        return None


def build_app() -> Any:
    ensure_fastapi()
    setup_json_logging()
    _logger = logging.getLogger("traderos.api")
    app = FastAPI(title="TraderOS API", version=version("traderos"))
    cors_origins = os.getenv("CORS_ORIGINS", "*")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins.split(",") if cors_origins != "*" else ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException):
        return _error_response(exc.status_code, exc.detail)

    @app.middleware("http")
    async def _request_logger(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = (time.perf_counter() - start) * 1000
        _logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(elapsed, 1),
            },
        )
        return response

    @app.middleware("http")
    async def _request_metrics(request: Request, call_next):
        if request.url.path in ("/metrics", "/v1/metrics"):
            return await call_next(request)
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = (time.perf_counter() - start) * 1000
        _metrics_service.counter("http_requests_total", 1)
        _metrics_service.observe("http_request_duration_ms", elapsed)
        return response

    @app.middleware("http")
    async def _rate_limit_middleware(request: Request, call_next):
        if request.url.path == "/metrics":
            return await call_next(request)
        client_ip = request.client.host if request.client else "unknown"
        if not _rate_limiter.check(client_ip):
            return _error_response(429, "Rate limit exceeded")
        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(_rate_limiter.remaining(client_ip))
        return response

    @app.middleware("http")
    async def _auth_middleware(request: Request, call_next):
        if request.url.path == "/metrics":
            return await call_next(request)
        try:
            _verify_api_key(request)
        except HTTPException as exc:
            return _error_response(exc.status_code, exc.detail)
        return await call_next(request)

    router = APIRouter(prefix="/v1")

    @app.get("/metrics")
    def get_prometheus_metrics():
        result = _prometheus_metrics()
        if result is not None:
            return result
        msg = "Prometheus client not installed; pip install traderos[monitoring]"
        return _error_response(501, msg)

    @router.get("/healthz")
    def get_liveness():
        # Liveness: process is up and can answer requests. No dependency
        # initialization, so this can never stall (OT-010).
        return {"status": "alive"}

    @router.get("/health")
    def get_health():
        # Readiness: bounded dependency initialization. A cold start that
        # exceeds the budget reports 503 "degraded" instead of hanging.
        try:
            orch = create_orchestrator(timeout=ORCHESTRATOR_READY_TIMEOUT)
        except TimeoutError:
            return _error_response(
                503, f"orchestrator not ready (build exceeded {ORCHESTRATOR_READY_TIMEOUT}s)"
            )
        return {
            "status": "ok",
            "mode": orch.mode.value,
            "running": orch.running,
            "ready": True,
        }

    @router.post("/backtest")
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

    @router.post("/orchestrator/start")
    def start_orchestrator():
        orch = create_orchestrator()
        orch.start()
        return {"status": "started", "mode": orch.mode.value}

    @router.post("/orchestrator/stop")
    def stop_orchestrator():
        orch = create_orchestrator()
        orch.stop()
        return {"status": "stopped"}

    @router.get("/orchestrator/status")
    def orchestrator_status():
        orch = create_orchestrator()
        return orch.get_status()

    @router.post("/papertrade/session")
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

    @router.get("/papertrade/sessions")
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

    @router.get("/audit")
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

    @router.get("/metrics")
    def get_metrics():
        orch = create_orchestrator()
        if not orch.running:
            return {"metrics": {}, "warning": "Orchestrator not running"}
        return {"metrics": orch.metrics.snapshot()}

    @router.get("/manifest")
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

    from traderos.interfaces.api.operator import register_operator_endpoints

    register_operator_endpoints(router, lambda: create_orchestrator())

    app.include_router(router)
    return app

# pyright: reportUntypedFunctionDecorator=false, reportUnusedFunction=false, reportOptionalCall=false, reportPrivateUsage=false

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime
from decimal import Decimal
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
from traderos.interfaces.api import events
from traderos.interfaces.api.security import auth_info
from traderos.interfaces.api.security import enforce_auth_boundary
from traderos.interfaces.api.security import require_admin
from traderos.interfaces.api.security import require_operate
from traderos.interfaces.api.security import require_read

if TYPE_CHECKING:
    from fastapi import APIRouter
    from fastapi import Depends
    from fastapi import FastAPI
    from fastapi import HTTPException
    from fastapi import Query
    from fastapi import Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import Response
    from pydantic import BaseModel

    _has_fastapi = True
else:
    try:
        from fastapi import APIRouter
        from fastapi import Depends
        from fastapi import FastAPI
        from fastapi import HTTPException
        from fastapi import Query
        from fastapi import Request
        from fastapi.exceptions import RequestValidationError
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import Response
        from pydantic import BaseModel  # type: ignore[assignment]

        _has_fastapi = True
    except ImportError:  # pragma: no cover
        _has_fastapi = False
        APIRouter = None  # type: ignore[assignment]
        BaseModel = object  # type: ignore[assignment]
        FastAPI = None  # type: ignore[assignment]
        HTTPException = None  # type: ignore[assignment]
        Query = None  # type: ignore[assignment]
        Request = None  # type: ignore[assignment]
        _has_fastapi = False
        APIRouter = None  # type: ignore[assignment]
        BaseModel = object  # type: ignore[assignment]
        FastAPI = None  # type: ignore[assignment]
        HTTPException = None  # type: ignore[assignment]
        Query = None  # type: ignore[assignment]
        Request = None  # type: ignore[assignment]
        Depends = None  # type: ignore[assignment]
        RequestValidationError = None  # type: ignore[assignment]
        CORSMiddleware = None  # type: ignore[assignment]
        Response = type("Response", (), {})  # type: ignore[assignment]


class TradeRequest(BaseModel):  # type: ignore[valid-type,misc]
    market_id: str
    side: str
    quantity: float


class BacktestRequest(BaseModel):  # type: ignore[valid-type,misc]
    strategy: str
    candles: int = 50
    symbol: str = "BTCUSDT"


class OperatorLoginRequest(BaseModel):  # type: ignore[valid-type,misc]
    username: str
    password: str


class CreatePaperSessionRequest(BaseModel):  # type: ignore[valid-type,misc]
    market_ids: list[str] | None = None


class PaperSessionResponse(BaseModel):  # type: ignore[valid-type,misc]
    id: str
    status: str
    capital: float


_orch_cache: dict[str, TradingOrchestrator] = {}
_metrics_service = PrometheusMetricsService()
_rate_limiter = RateLimiter(
    max_requests=int(os.getenv("RATE_LIMIT_MAX", "100")), window_seconds=60.0
)
ORCHESTRATOR_READY_TIMEOUT = float(os.getenv("ORCHESTRATOR_READY_TIMEOUT", "5.0"))


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


def reset_rate_limiter() -> None:
    """Clear per-IP request buckets. Used by tests and hot-reload tooling."""
    _rate_limiter._buckets.clear()


def ensure_fastapi() -> None:
    if not _has_fastapi:  # pragma: no cover
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
    except ImportError:  # pragma: no cover
        return None


def build_app() -> Any:
    ensure_fastapi()
    setup_json_logging()
    _logger = logging.getLogger("traderos.api")
    app = FastAPI(
        title="TraderOS API",
        version=version("traderos"),
        description=(
            "TraderOS operator + retail API. Error envelope: every error "
            'response is {"error": {"code": <http_status>, "message": '
            "<human-readable>}} — including FastAPI 422 validation failures."
        ),
    )
    cors_origins = os.getenv("CORS_ORIGINS", "").strip()
    if cors_origins == "*":
        allowed_origins = ["*"]
    else:
        allowed_origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException):
        return _error_response(exc.status_code, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(request: Request, exc: RequestValidationError):
        _logger.warning("validation_error", extra={"errors": exc.errors()})
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
        message = "Request validation failed"
        if loc:
            message += f" at {loc}"
        message += f": {first.get('msg', 'invalid input')}"
        return _error_response(422, message)

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
    async def _auth_boundary_middleware(request: Request, call_next):
        """A1: fail-closed boundary auth.

        Runs before route dispatch so an endpoint that omits its role
        dependency is still denied whenever authentication is required.
        Public probes (healthz, auth/me) remain reachable; the dashboard static
        bundle is read-only and served separately.
        """
        try:
            enforce_auth_boundary(request)
        except HTTPException:
            return _error_response(401, "Unauthorized: a valid API key is required")
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

    @router.get("/auth/me")
    def get_auth_me(request: Request):
        # Self-describing auth state for the login screen and ops tooling.
        # Never requires a key; returns whether authentication is required
        # and whether the presented credential (API key or session token) is
        # valid.
        return auth_info(request)

    @router.post("/auth/login")
    def post_auth_login(req: OperatorLoginRequest):
        # Operator login: validates a username/password against the PG-backed
        # user store and mints a server-side session token (fail-closed, same
        # PBKDF2 + constant-time verification the retail seam uses). The token
        # is short-lived, revocable, and stored hashed server-side — the
        # dashboard holds this session token, not a static API key, replacing
        # the localStorage API-key interim model (WP8).
        orch = create_orchestrator()
        account = getattr(orch, "account_service", None)
        if account is None:
            raise HTTPException(501, "Account service not configured")
        result = account.authenticate(req.username, req.password)
        if not result.authenticated or result.user is None:
            if getattr(orch, "audit", None) is not None:
                orch.audit.record("operator.login_denied", req.username, "api", "denied")
            raise HTTPException(401, "Invalid username or password")
        token, session = account.create_session(result.user)
        if getattr(orch, "audit", None) is not None:
            orch.audit.record("operator.login", req.username, "api", "ok")
        return {
            "token": token,
            "token_type": "session",
            "expires_at": session.expires_at.isoformat(),
            "user": {"username": result.user.username, "role": result.user.role.value},
        }

    @router.post("/auth/logout")
    def operator_logout(request: Request):
        orch = create_orchestrator()
        account = getattr(orch, "account_service", None)
        token = request.headers.get("X-Session-Token")
        if account is not None and token:
            user = account.validate_session(token)
            account.revoke_session(token)
            if getattr(orch, "audit", None) is not None:
                orch.audit.record("operator.logout", user.username if user else "-", "api", "ok")
        return {"logged_out": True}

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

    @router.post("/backtest", dependencies=[Depends(require_read)])
    def run_backtest(req: BacktestRequest):
        # Honest backtest: the engine runs against the REAL ingested candle
        # series for the requested symbol (DataIngestionService) — the same
        # data the live loop consumes — never synthetic constant candles
        # fabricated in the handler. Fails closed (404/503) when the symbol is
        # unknown or no data is available, so a UI can never display a
        # fabricated-in-place result.
        strat_cls = strategy_registry.get(req.strategy)
        if strat_cls is None:
            raise HTTPException(404, f"Strategy '{req.strategy}' not found")
        orch = create_orchestrator()
        if orch.data_ingestion is None:  # pragma: no cover — always set by factory
            raise HTTPException(503, "Data ingestion not configured")
        raw = orch.data_ingestion.fetch_all(limit=req.candles)
        rows = raw.get(req.symbol)
        if not rows:
            raise HTTPException(404, f"Unknown or empty market symbol '{req.symbol}'")
        mid = next(
            (s.market_id for s in orch.data_ingestion.sources if s.symbol == req.symbol),
            None,
        )
        if mid is None:  # pragma: no cover — a fetched symbol always has a source
            raise HTTPException(404, f"No registered data source for market '{req.symbol}'")

        from traderos.domain.entities import OHLCV
        from traderos.domain.entities import Candle
        from traderos.domain.entities import Timeframe

        candles: list[Candle] = []
        for r in rows[: req.candles]:
            ts = r.get("timestamp")
            ts_val: Any = ts
            if isinstance(ts_val, str):
                try:
                    ts_val = datetime.fromisoformat(ts_val)
                except ValueError:  # pragma: no cover — ingest always emits ISO
                    ts_val = None
            candles.append(
                Candle(
                    market_id=mid,
                    ohlcv=OHLCV(
                        open=Decimal(str(r["open"])),
                        high=Decimal(str(r["high"])),
                        low=Decimal(str(r["low"])),
                        close=Decimal(str(r["close"])),
                        volume=Decimal(str(r.get("volume", 0))),
                    ),
                    timestamp=ts_val,
                    timeframe=Timeframe.HOUR_1,
                    source=req.symbol,
                )
            )
        if not candles:  # pragma: no cover — non-empty rows yield non-empty candles
            raise HTTPException(404, f"No candle data available for '{req.symbol}'")
        strategy = strat_cls()
        svc = BacktestingService(execution=ExecutionService())
        result, _ = svc.run(strategy, candles, mid)
        m = result.metrics
        # Persist the result so it appears in the strategy review / history
        # (surfaces None when no durable repo is wired — consumers can treat
        # that as "result not retained").
        recorded = None
        if orch.strategy_catalog is not None:
            recorded = orch.strategy_catalog.record_backtest(
                req.strategy,
                mid,
                result.metrics,
                result.equity_curve,
                result.period_start,
                result.period_end,
            )
        return {
            "strategy": req.strategy,
            "symbol": req.symbol,
            "candles": len(candles),
            "recorded": recorded is not None,
            "total_return": m.total_return,
            "sharpe_ratio": m.sharpe_ratio,
            "max_drawdown": m.max_drawdown,
            "win_rate": m.win_rate,
            "sortino_ratio": m.sortino_ratio,
            "calmar_ratio": m.calmar_ratio,
        }

    @router.get("/backtest/history", dependencies=[Depends(require_read)])
    def backtest_history(strategy: str, limit: int = Query(20, ge=1, le=100)):
        orch = create_orchestrator()
        if orch.strategy_catalog is None:  # pragma: no cover — always set by factory
            raise HTTPException(503, "Strategy catalog not configured")
        try:
            return orch.strategy_catalog.history(strategy, limit=limit)
        except Exception as exc:
            from traderos.domain.exceptions import DomainError

            if isinstance(exc, DomainError):
                raise HTTPException(404, str(exc)) from exc
            raise  # pragma: no cover — history only raises DomainError

    @router.post("/orchestrator/start", dependencies=[Depends(require_admin)])
    def start_orchestrator():
        orch = create_orchestrator()
        orch.start()
        events.publish_event("orchestrator", {"running": True, "mode": orch.mode.value})
        return {"status": "started", "mode": orch.mode.value}

    @router.post("/orchestrator/stop", dependencies=[Depends(require_admin)])
    def stop_orchestrator():
        orch = create_orchestrator()
        orch.stop()
        events.publish_event("orchestrator", {"running": False, "mode": orch.mode.value})
        return {"status": "stopped"}

    @router.get("/orchestrator/status", dependencies=[Depends(require_read)])
    def orchestrator_status():
        orch = create_orchestrator()
        return orch.get_status()

    @router.post("/papertrade/session", dependencies=[Depends(require_operate)])
    def create_paper_session(req: CreatePaperSessionRequest | None = None):
        orch = create_orchestrator()
        if orch.paper is None:
            raise HTTPException(400, "Paper trading not configured")  # pragma: no cover
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

    @router.get("/papertrade/sessions", dependencies=[Depends(require_read)])
    def list_paper_sessions(limit: int | None = Query(None, ge=1), offset: int = Query(0, ge=0)):
        orch = create_orchestrator()
        if orch.paper is None:
            return {"sessions": []}  # pragma: no cover
        sessions = orch.paper.list_sessions()
        sessions = sessions[offset:]
        if limit is not None:
            sessions = sessions[:limit]
        return {
            "sessions": [
                {"id": str(s.id), "status": s.status.value, "capital": s.current_capital}
                for s in sessions
            ]
        }

    @router.get("/audit", dependencies=[Depends(require_read)])
    def get_audit(limit: int = Query(10, ge=1, le=100), offset: int = Query(0, ge=0)):
        orch = create_orchestrator()
        return {
            "entries": [
                {
                    "action": e.action,
                    "actor": e.actor,
                    "resource": e.resource,
                    "timestamp": e.timestamp.isoformat(),
                }
                for e in orch.audit.get_entries(limit=limit, offset=offset)
            ]
        }

    @router.get("/metrics", dependencies=[Depends(require_read)])
    def get_metrics():
        orch = create_orchestrator()
        if not orch.running:
            return {"metrics": {}, "warning": "Orchestrator not running"}
        return {"metrics": orch.metrics.snapshot()}

    @router.get("/manifest", dependencies=[Depends(require_read)])
    def get_manifest(
        service: str | None = None,
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ):
        orch = create_orchestrator()
        return {
            "runs": [
                {
                    "service": e.service,
                    "action": e.action,
                    "status": e.status,
                    "duration_ms": e.duration_ms,
                }
                for e in orch.run_manifest.get_runs(service=service, limit=limit, offset=offset)
            ]
        }

    from traderos.interfaces.api.operator import register_operator_endpoints

    register_operator_endpoints(router, lambda: create_orchestrator())
    from traderos.interfaces.api.retail import register_retail_endpoints

    register_retail_endpoints(router, lambda: create_orchestrator())
    from traderos.interfaces.api.attribution import register_attribution_endpoints

    register_attribution_endpoints(router, lambda: create_orchestrator())

    # WP9 — Market Overview + Research Lab served from the real runtime services
    # (DataIngestionService + AnalysisService + ResearchService + strategy
    # registry), each endpoint gated by the shared read permission boundary.
    from traderos.interfaces.api.market import register_market_research_endpoints

    register_market_research_endpoints(router, lambda: create_orchestrator())

    # WP8 — operator login seam: install the X-Session-Token -> role resolver so
    # the boundary and every permission dependency accept PG-backed sessions.
    from traderos.infrastructure.auth import Role
    from traderos.interfaces.api import security

    def _resolve_session_role(token: str) -> Role | None:
        orch = create_orchestrator()
        account = getattr(orch, "account_service", None)
        if account is None:
            return None
        user = account.validate_session(token)
        if user is None:
            return None
        return {
            "admin": Role.ADMIN,
            "operator": Role.OPERATOR,
            "viewer": Role.VIEWER,
        }.get(user.role.value)

    security.set_session_resolver(_resolve_session_role)

    app.include_router(router)

    # --- Finish Line Dashboard (static SPA) ---
    from pathlib import Path

    from fastapi.responses import RedirectResponse
    from starlette.staticfiles import StaticFiles

    _dashboard_dir = Path(__file__).parent / "dashboard"

    @app.get("/", include_in_schema=False)
    def get_dashboard_root():
        return RedirectResponse(url="/dashboard/")

    app.mount(
        "/dashboard",
        StaticFiles(directory=_dashboard_dir, html=True),
        name="dashboard",
    )
    return app

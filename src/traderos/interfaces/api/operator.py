# pyright: reportUntypedFunctionDecorator=false, reportUnusedFunction=false, reportOptionalCall=false, reportPrivateUsage=false, reportUntypedBaseClass=false

"""Programme C (C1) — Operator surface REST endpoints.

Exposes the operator-facing dashboard: positions, orders, trades, portfolio,
equity curve, pnl, kill switch, preflight, the enforced operator workflow and
the strategy catalog. All handlers resolve the orchestrator through the
provider injected at registration time.
"""

from __future__ import annotations

import asyncio
import json
import queue
from collections.abc import AsyncIterator
from collections.abc import Callable
from datetime import UTC
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from traderos.application.orchestrator import TradingMode
from traderos.application.orchestrator import TradingOrchestrator
from traderos.domain.services.notification_service import NotificationLevel
from traderos.domain.services.operator_workflow import OperatorStep
from traderos.domain.services.operator_workflow import WorkflowError
from traderos.domain.services.session_report import SessionReportService
from traderos.domain.services.strategy_management import StrategyLifecycleError
from traderos.interfaces.api.events import EventBroker
from traderos.interfaces.api.events import get_broker
from traderos.interfaces.api.events import publish_event
from traderos.interfaces.api.security import require_admin
from traderos.interfaces.api.security import require_operate
from traderos.interfaces.api.security import require_read

OrchestratorProvider = Callable[[], TradingOrchestrator]


class StrategyCreateRequest(BaseModel):
    name: str
    template: str
    params: dict | None = None


class StrategyCloneRequest(BaseModel):
    name: str


class StrategyCompareRequest(BaseModel):
    names: list[str]


class WorkflowAdvanceRequest(BaseModel):
    step: str
    actor: str = "operator"
    strategy: str | None = None
    session_id: str | None = None
    dry_run: bool = False


def _cash(orch: TradingOrchestrator) -> float:
    if orch.mode == TradingMode.LIVE:
        try:
            return float(orch.broker.get_account_balance())
        except Exception:  # noqa: BLE001 — broker connectivity may fail
            return orch.default_cash
    return orch.default_cash


def _catalog(orch: TradingOrchestrator):
    if orch.strategy_catalog is None:
        raise HTTPException(501, "Strategy catalog not configured")
    return orch.strategy_catalog


def _lifecycle_error(exc: Exception) -> HTTPException:
    return HTTPException(400, str(exc))


def _live_snapshot(orch_provider: OrchestratorProvider) -> dict[str, Any]:
    """Cheap snapshot of the operator surface for the SSE feed.

    Never blocks a request: a cold orchestrator build or a connectivity
    failure degrades the snapshot to ``ready=False`` and the dashboard falls
    back to its REST polling.
    """
    try:
        orch = orch_provider()
        workflow: dict[str, Any] | None = None
        if orch.operator_session is not None:
            session = orch.operator_session
            workflow = {
                "current_step": session.current_step.value if session.current_step else None,
                "next_step": session.next_step.value if session.next_step else None,
                "status": session.status.value,
            }
        kill_switch = orch.risk_service.kill_switch
        verdict = kill_switch.can_trade()
        return {
            "ready": True,
            "mode": orch.mode.value,
            "running": orch.running,
            "workflow": workflow,
            "kill_switch": {
                "engaged": not verdict.allowed,
                "circuit_open": kill_switch.circuit_open,
                "reason": verdict.reason,
            },
            "timestamp": datetime.now(UTC).isoformat(),
        }
    except Exception:  # noqa: BLE001 — degraded snapshot is a first-class state
        return {"ready": False, "timestamp": datetime.now(UTC).isoformat()}


def _sse(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, default=str)}\n\n"


async def event_stream(
    broker: EventBroker,
    orch_provider: OrchestratorProvider,
    wait_timeout: float = 15.0,
) -> AsyncIterator[str]:
    """Async generator backing the ``/v1/events`` SSE feed.

    Yields a snapshot frame first, then every event published to ``broker``.
    If nothing arrives within ``wait_timeout`` seconds a keepalive comment
    frame is emitted. Always unsubscribes on close/error.
    """
    sub = broker.subscribe()
    try:
        yield _sse("snapshot", _live_snapshot(orch_provider))
        while True:
            try:
                # queue.Queue.get(block=True, timeout=...) run off-loop
                event = await asyncio.to_thread(sub.get, True, wait_timeout)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            yield _sse(event.get("type", "state"), event.get("data", {}))
    finally:
        broker.unsubscribe(sub)


def register_operator_endpoints(router: APIRouter, orch_provider: OrchestratorProvider) -> None:
    # --- real-time feed (SSE) ---

    @router.get("/events", dependencies=[Depends(require_read)])
    async def stream_events():
        return StreamingResponse(
            event_stream(get_broker(), orch_provider),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # --- positions / orders / trades / portfolio ---

    @router.get("/positions", dependencies=[Depends(require_read)])
    def get_positions():
        orch = orch_provider()
        positions = orch.portfolio_service.position_repo.list()
        return {
            "positions": [
                {
                    "id": str(p.id),
                    "market_id": str(p.market_id),
                    "quantity": p.quantity,
                    "entry_price": p.entry_price,
                    "current_price": p.current_price,
                    "pnl": p.pnl,
                    "realized_pnl": p.realized_pnl,
                    "updated_at": p.updated_at.isoformat(),
                }
                for p in positions
            ]
        }

    @router.get("/orders", dependencies=[Depends(require_read)])
    def get_orders():
        orch = orch_provider()
        open_orders = orch.broker.get_open_orders()
        return {"orders": open_orders}

    @router.get("/trades", dependencies=[Depends(require_read)])
    def get_trades(limit: int = 100):
        orch = orch_provider()
        trades = sorted(
            orch.portfolio_service.trade_repo.list(),
            key=lambda t: t.created_at,
            reverse=True,
        )[:limit]
        return {
            "trades": [
                {
                    "id": str(t.id),
                    "market_id": str(t.market_id),
                    "side": t.side.value,
                    "quantity": t.quantity,
                    "price": t.price,
                    "status": t.status.value,
                    "filled_price": t.filled_price,
                    "filled_at": t.filled_at.isoformat() if t.filled_at else None,
                    "external_order_id": t.external_order_id,
                    "created_at": t.created_at.isoformat(),
                }
                for t in trades
            ]
        }

    @router.get("/portfolio", dependencies=[Depends(require_read)])
    def get_portfolio():
        orch = orch_provider()
        cash = _cash(orch)
        summary = orch.portfolio_service.get_summary(cash)
        return {
            "total_equity": summary.total_equity,
            "cash": summary.cash,
            "positions_value": summary.positions_value,
            "total_pnl": summary.total_pnl,
            "position_count": summary.position_count,
        }

    @router.get("/equity-curve", dependencies=[Depends(require_read)])
    def get_equity_curve():
        orch = orch_provider()
        cash = _cash(orch)
        positions = sorted(orch.portfolio_service.position_repo.list(), key=lambda p: p.updated_at)
        running = cash
        points: list[dict[str, Any]] = []
        for p in positions:
            running += p.realized_pnl
            open_unrealized = sum(q.pnl for q in positions if q.updated_at <= p.updated_at)
            points.append(
                {
                    "timestamp": p.updated_at.isoformat(),
                    "equity": round(running + open_unrealized, 2),
                }
            )
        summary = orch.portfolio_service.get_summary(cash)
        points.append(
            {"timestamp": datetime.now(UTC).isoformat(), "equity": round(summary.total_equity, 2)}
        )
        return {"points": points}

    @router.get("/pnl", dependencies=[Depends(require_read)])
    def get_pnl():
        orch = orch_provider()
        positions = orch.portfolio_service.position_repo.list()
        realized = sum(p.realized_pnl for p in positions)
        unrealized = sum(p.pnl for p in positions)
        return {
            "realized_pnl": round(realized, 2),
            "unrealized_pnl": round(unrealized, 2),
            "total_pnl": round(realized + unrealized, 2),
        }

    # --- risk / kill switch / preflight ---

    @router.get("/kill-switch", dependencies=[Depends(require_read)])
    def get_kill_switch():
        orch = orch_provider()
        ks = orch.risk_service.kill_switch
        verdict = ks.can_trade()
        return {
            "engaged": not verdict.allowed,
            "reason": verdict.reason,
            "circuit_open": ks.circuit_open,
            "consecutive_failures": ks.consecutive_failures,
            "daily_realized_pnl": ks.daily_realized_pnl,
        }

    @router.post("/kill-switch/engage", dependencies=[Depends(require_admin)])
    def engage_kill_switch():
        orch = orch_provider()
        orch.risk_service.kill_switch.engage()
        orch.notifications.send(
            NotificationLevel.CRITICAL,
            "Kill switch engaged",
            "Manual operator action — all trading halted.",
            metadata={"source": "operator_api"},
        )
        publish_event("kill_switch", {"engaged": True})
        return {"engaged": True}

    @router.post("/kill-switch/disengage", dependencies=[Depends(require_admin)])
    def disengage_kill_switch():
        orch = orch_provider()
        orch.risk_service.kill_switch.disengage()
        orch.notifications.send(
            NotificationLevel.WARNING,
            "Kill switch disengaged",
            "Circuit cleared — trading may resume.",
            metadata={"source": "operator_api"},
        )
        publish_event("kill_switch", {"engaged": False})
        return {"engaged": False}

    @router.get("/preflight", dependencies=[Depends(require_read)])
    def get_preflight():
        orch = orch_provider()
        if orch.preflight_service is None:
            raise HTTPException(501, "Preflight not configured")
        verdict = orch.preflight_service.check(live_mode=orch.mode == TradingMode.LIVE)
        return {
            "passed": verdict.passed,
            "checks": verdict.checks,
            "failures": verdict.failures,
            "timestamp": verdict.timestamp.isoformat(),
        }

    @router.get("/readiness", dependencies=[Depends(require_read)])
    def get_readiness():
        orch = orch_provider()
        checks: dict[str, Any] = {}
        if orch.preflight_service is not None:
            verdict = orch.preflight_service.check(live_mode=orch.mode == TradingMode.LIVE)
            checks["preflight"] = verdict.passed
        checks["data_feeds"] = len(orch.data_ingestion.sources) if orch.data_ingestion else 0
        try:
            checks["broker"] = bool(orch.broker.get_account_balance())
        except Exception:  # noqa: BLE001 — connectivity failure is a real readiness signal
            checks["broker"] = False
        ready = all(v is True if isinstance(v, bool) else v > 0 for v in checks.values())
        return {"ready": bool(ready), "checks": checks}

    # --- operator workflow ---

    @router.get("/workflow", dependencies=[Depends(require_read)])
    def get_workflow():
        orch = orch_provider()
        session = orch.operator_session
        if session is None:
            return {"current_step": None, "status": "idle", "history": []}
        return {
            "current_step": session.current_step.value if session.current_step else None,
            "next_step": session.next_step.value if session.next_step else None,
            "status": session.status.value,
            "session_id": session.session_id,
            "history": session.history(),
        }

    @router.post("/workflow/advance", dependencies=[Depends(require_operate)])
    def advance_workflow(req: WorkflowAdvanceRequest):
        orch = orch_provider()
        session = orch.operator_session
        if session is None:
            raise HTTPException(501, "Operator workflow not configured")
        try:
            step = OperatorStep(req.step)
        except ValueError:
            raise HTTPException(400, f"Unknown workflow step '{req.step}'") from None
        context: dict[str, Any] = {}
        if req.strategy:
            context["strategy"] = req.strategy
        if req.session_id:
            context["session_id"] = req.session_id
        if req.dry_run:
            context["dry_run"] = True
        try:
            outcome = session.perform(step, actor=req.actor, **context)
        except WorkflowError as exc:
            raise HTTPException(409, str(exc)) from exc
        publish_event(
            "workflow",
            {
                "step": outcome.step.value,
                "ok": outcome.ok,
                "detail": outcome.detail,
            },
        )
        return {
            "step": outcome.step.value,
            "ok": outcome.ok,
            "result": outcome.result,
            "detail": outcome.detail,
            "current_step": session.current_step.value if session.current_step else None,
        }

    # --- live trading verification (WP-2 controlled pilot) ---

    @router.get("/live/check", dependencies=[Depends(require_read)])
    def get_live_readiness():
        orch = orch_provider()
        if orch.live_readiness is None:
            raise HTTPException(501, "Live readiness not configured")
        return orch.live_readiness.check().to_dict()

    # --- strategy catalog (C3) ---

    @router.get("/strategies", dependencies=[Depends(require_read)])
    def list_catalog_strategies():
        orch = orch_provider()
        catalog = _catalog(orch)
        return {
            "strategies": [
                {
                    "name": s.name,
                    "template": s.template,
                    "params": s.params,
                    "status": s.status.value,
                    "version": s.version,
                    "created_at": s.created_at.isoformat(),
                }
                for s in catalog.list()
            ]
        }

    @router.post("/strategies", dependencies=[Depends(require_operate)])
    def create_catalog_strategy(req: StrategyCreateRequest):
        orch = orch_provider()
        catalog = _catalog(orch)
        try:
            created = catalog.create(req.name, req.template, req.params)
        except StrategyLifecycleError as exc:
            raise _lifecycle_error(exc) from exc
        return {
            "name": created.name,
            "template": created.template,
            "status": created.status.value,
        }

    @router.post("/strategies/compare", dependencies=[Depends(require_operate)])
    def compare_catalog_strategies(req: StrategyCompareRequest):
        orch = orch_provider()
        catalog = _catalog(orch)
        try:
            comparison = catalog.compare(req.names, candles=50)
        except StrategyLifecycleError as exc:
            raise _lifecycle_error(exc) from exc
        return {
            "ranking": comparison.ranking,
            "metrics": comparison.metrics,
        }

    @router.get("/strategies/{name}", dependencies=[Depends(require_read)])
    def get_catalog_strategy(name: str):
        orch = orch_provider()
        catalog = _catalog(orch)
        strategy = catalog.get(name)
        if strategy is None:
            raise HTTPException(404, f"Strategy '{name}' not found")
        return {
            "name": strategy.name,
            "template": strategy.template,
            "params": strategy.params,
            "status": strategy.status.value,
            "version": strategy.version,
            "created_at": strategy.created_at.isoformat(),
        }

    @router.get("/strategies/{name}/review", dependencies=[Depends(require_read)])
    def review_catalog_strategy(name: str):
        orch = orch_provider()
        catalog = _catalog(orch)
        if catalog.get(name) is None:
            raise HTTPException(404, f"Strategy '{name}' not found")
        return catalog.review(name)

    @router.post("/strategies/{name}/enable", dependencies=[Depends(require_operate)])
    def enable_catalog_strategy(name: str):
        orch = orch_provider()
        catalog = _catalog(orch)
        try:
            updated = catalog.enable(name)
        except StrategyLifecycleError as exc:
            raise _lifecycle_error(exc) from exc
        return {"name": updated.name, "status": updated.status.value}

    @router.post("/strategies/{name}/disable", dependencies=[Depends(require_operate)])
    def disable_catalog_strategy(name: str):
        orch = orch_provider()
        catalog = _catalog(orch)
        try:
            updated = catalog.disable(name)
        except StrategyLifecycleError as exc:
            raise _lifecycle_error(exc) from exc
        return {"name": updated.name, "status": updated.status.value}

    @router.post("/strategies/{name}/promote", dependencies=[Depends(require_operate)])
    def promote_catalog_strategy(name: str):
        orch = orch_provider()
        catalog = _catalog(orch)
        try:
            updated = catalog.promote(name)
        except StrategyLifecycleError as exc:
            raise _lifecycle_error(exc) from exc
        return {"name": updated.name, "status": updated.status.value}

    @router.post("/strategies/{name}/archive", dependencies=[Depends(require_operate)])
    def archive_catalog_strategy(name: str):
        orch = orch_provider()
        catalog = _catalog(orch)
        try:
            updated = catalog.archive(name)
        except StrategyLifecycleError as exc:
            raise _lifecycle_error(exc) from exc
        return {"name": updated.name, "status": updated.status.value}

    @router.post("/strategies/{name}/clone", dependencies=[Depends(require_operate)])
    def clone_catalog_strategy(name: str, req: StrategyCloneRequest):
        orch = orch_provider()
        catalog = _catalog(orch)
        try:
            cloned = catalog.clone(name, req.name)
        except StrategyLifecycleError as exc:
            raise _lifecycle_error(exc) from exc
        return {"name": cloned.name, "template": cloned.template, "status": cloned.status.value}

    # --- session report (C4) ---

    @router.get("/reports/session", dependencies=[Depends(require_read)])
    def get_session_report(fmt: str = "json"):
        orch = orch_provider()
        session = orch.operator_session
        if session is None:
            raise HTTPException(501, "Operator workflow not configured")
        report = SessionReportService(
            workflow=session.workflow,
            portfolio=orch.portfolio_service,
            risk=orch.risk_service,
            strategy_catalog=orch.strategy_catalog,
            cash=_cash(orch),
        ).generate()
        if fmt == "markdown":
            return Response(content=report.to_markdown(), media_type="text/markdown")
        return report.to_dict()

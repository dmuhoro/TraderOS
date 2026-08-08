# pyright: reportUntypedFunctionDecorator=false, reportUnusedFunction=false, reportOptionalCall=false, reportPrivateUsage=false, reportUntypedBaseClass=false

"""B4 — Causal attribution / regulator replay endpoint.

A read-only operator surface that runs the REAL ReplayService over the durable
audit trail (signal.generated -> decision.made -> order.placed -> trade.fill)
and recomputes per-fill realized PnL with FIFO matching. The start/end window
controls the replay; output mirrors the replay internals so a regulator can
audit the causal chain, including which orders were blocked and why.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query

from traderos.application.orchestrator import TradingOrchestrator
from traderos.domain.services.replay_service import ReplayService
from traderos.interfaces.api.security import require_read

OrchestratorProvider = Callable[[], TradingOrchestrator]


def _replay(orch: TradingOrchestrator) -> ReplayService:
    return ReplayService(audit=orch.audit, trade_repo=orch.portfolio_service.trade_repo)


def _fill_dict(fill) -> dict | None:
    if fill is None:
        return None
    return {
        "trade_id": fill.trade_id,
        "side": fill.side,
        "qty": fill.qty,
        "price": fill.price,
        "filled_at": fill.filled_at,
        "realized_pnl": fill.realized_pnl,
        "decision": fill.decision,
        "decision_reason": fill.decision_reason,
        "order_status": fill.order_status,
        "order_id": fill.order_id,
    }


def _chain_dict(chain) -> dict:
    return {
        "signal_id": chain.signal_id,
        "market_id": chain.market_id,
        "signal_at": chain.signal_at.isoformat(),
        "strategy": chain.strategy,
        "direction": chain.direction,
        "confidence": chain.confidence,
        "blocked": chain.blocked,
        "complete": chain.complete,
        "steps": [
            {"action": s.action, "at": s.at.isoformat(), "actor": s.actor} for s in chain.steps
        ],
        "fill": _fill_dict(chain.fill),
    }


def register_attribution_endpoints(router: APIRouter, orch_provider: OrchestratorProvider) -> None:
    @router.get("/attribution/replay", dependencies=[Depends(require_read)])
    def get_replay(
        start: Annotated[datetime, Query()],
        end: Annotated[datetime, Query()],
    ):
        if end < start:
            raise HTTPException(422, "end must be >= start")
        orch = orch_provider()
        report = _replay(orch).replay_day(start, end)
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "total_realized_pnl": report.total_realized_pnl,
            "total_blocked": report.total_blocked,
            "total_unfilled": report.total_unfilled,
            "chains": [_chain_dict(c) for c in report.chains],
            "mode": orch.mode.value,
        }

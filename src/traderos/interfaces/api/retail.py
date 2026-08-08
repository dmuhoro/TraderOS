# pyright: reportUntypedFunctionDecorator=false, reportUnusedFunction=false, reportOptionalCall=false, reportPrivateUsage=false, reportUntypedBaseClass=false

"""B3 — Retail account seam (sessions, per-trader order entry).

The retail surface authenticates callers with *sessions* issued by the real
AccountService (PBKDF2 + constant-time compare, fail-closed). Orders placed here
are routed to the SAME cycle-executor submission the live loop uses: the
per-user risk authorize() gate runs first and the broker is only contacted when
the gate explicitly allows the order. A session-less caller, an unknown trader,
or a trader without an engaged risk profile can never reach the broker, and
every outcome is written to the audit trail (replayable).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from pydantic import BaseModel

from traderos.application.orchestrator import TradingMode
from traderos.application.orchestrator import TradingOrchestrator
from traderos.domain.entities.user import User
from traderos.domain.entities.user import UserRole
from traderos.domain.services.account_service import AccountService

OrchestratorProvider = Callable[[], TradingOrchestrator]


class RegisterRequest(BaseModel):  # type: ignore[valid-type,misc]
    username: str
    password: str
    role: str = UserRole.VIEWER.value


class LoginRequest(BaseModel):  # type: ignore[valid-type,misc]
    username: str
    password: str


class RetailOrderRequest(BaseModel):  # type: ignore[valid-type,misc]
    market_id: str
    side: str
    quantity: float
    close_price: float


def _account(orch: TradingOrchestrator) -> AccountService:
    if orch.account_service is None:
        raise HTTPException(501, "Account service not configured")  # pragma: no cover
    return orch.account_service


def _parse_role(role: str) -> UserRole:
    try:
        return UserRole(role)
    except ValueError as exc:
        raise HTTPException(422, f"Unknown role '{role}'") from exc


def _profile_for(orch: TradingOrchestrator, user: User) -> dict:
    """Read-only per-trader risk rails for the retail view.

    Reads the SAME per-user resolver the orchestrator's risk gate enforces, so
    the trader sees exactly the rails that will apply at submission time.
    """
    resolver = getattr(orch.risk_service, "user_resolver", None)
    if resolver is None or getattr(resolver, "resolve", None) is None:
        return {"configured": False, "engaged": False}
    profile = resolver.resolve(str(user.id))
    if profile is None:
        return {"configured": False, "engaged": False}
    return {
        "configured": True,
        "engaged": profile.engaged,
        "max_gross_exposure": profile.max_gross_exposure,
        "max_position_size": profile.max_position_size,
        "max_positions_total": profile.max_positions_total,
        "daily_loss_pct": profile.daily_loss_pct,
        "allowed_market_count": len(profile.allowed_markets),
    }


def register_retail_endpoints(router: APIRouter, orch_provider: OrchestratorProvider) -> None:
    def _session_token(request: Request) -> str | None:
        return request.headers.get("x-session-token")

    def require_user(request: Request) -> User:
        """Fail-closed session dependency: no token, bad token, or expired
        session all deny before any handler body runs."""
        token = _session_token(request)
        if not token:
            raise HTTPException(401, "Unauthorized: a session token is required")
        user = _account(orch_provider()).validate_session(token)
        if user is None:
            raise HTTPException(401, "Unauthorized: invalid or expired session")
        return user

    @router.post("/retail/register")
    def retail_register(req: RegisterRequest):
        orch = orch_provider()
        user = _account(orch).create_user(req.username, req.password, role=_parse_role(req.role))
        if user is None:
            raise HTTPException(409, "Username already registered")
        return {"id": str(user.id), "username": user.username, "role": user.role.value}

    @router.post("/retail/login")
    def retail_login(req: LoginRequest):
        orch = orch_provider()
        result = _account(orch).authenticate(req.username, req.password)
        if not result.authenticated or result.user is None:
            raise HTTPException(401, "Invalid username or password")
        token, _ = _account(orch).create_session(result.user)
        return {
            "token": token,
            "user": {"id": str(result.user.id), "username": result.user.username},
        }

    @router.post("/retail/logout")
    def retail_logout(
        request: Request,
        user: User = Depends(require_user),
    ):
        orch = orch_provider()
        token = _session_token(request)
        if token:
            _account(orch).revoke_session(token)
        return {"logged_out": True}

    @router.get("/retail/me")
    def retail_me(user: User = Depends(require_user)):
        orch = orch_provider()
        return {
            "user": {
                "id": str(user.id),
                "username": user.username,
                "role": user.role.value,
            },
            "risk_profile": _profile_for(orch, user),
            "orders_enabled": orch.mode == TradingMode.PAPER,
        }

    @router.post("/retail/orders")
    def retail_orders(
        req: RetailOrderRequest,
        user: User = Depends(require_user),
    ):
        orch = orch_provider()
        # Fail-closed: retail order entry is paper-only. On live/backtest it
        # refuses rather than pretending a submission path exists.
        if orch.mode != TradingMode.PAPER:
            raise HTTPException(
                403,
                f"Retail order entry is available on paper mode only (mode={orch.mode.value})",
            )
        try:
            market_id = uuid.UUID(req.market_id)
        except ValueError as exc:
            raise HTTPException(422, f"Invalid market_id '{req.market_id}'") from exc
        result = orch.submit_retail_order(
            market_id,
            side=req.side,
            quantity=req.quantity,
            close_price=req.close_price,
            user_id=str(user.id),
        )
        if not result.allowed:
            raise HTTPException(400, f"Order blocked: {result.reason}")
        return {
            "allowed": True,
            "order_id": result.order_id,
            "signal_id": result.signal_id,
            "reason": result.reason,
        }

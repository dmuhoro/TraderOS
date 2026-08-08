# pyright: reportUntypedFunctionDecorator=false, reportUnusedFunction=false, reportOptionalCall=false, reportPrivateUsage=false, reportUntypedBaseClass=false

"""Pydantic response models for the in-scope operator endpoints.

These are the typed contract for the CURRENT dashboard + the near-term friend
beta. Endpoints NOT in this module deliberately remain untyped — matching the
scope decision in FRONTEND_READINESS_AUDIT.md (7 endpoints now, not all 44).

Every response here mirrors the exact field names the dashboard's app.js
already reads. Adding a response_model makes FastAPI validate the handler's
dict and exposes the shape in /openapi.json, which is the real prerequisite
for a typed frontend client.
"""

from __future__ import annotations

from pydantic import BaseModel


class PortfolioResponse(BaseModel):
    total_equity: float
    cash: float
    positions_value: float
    total_pnl: float
    position_count: int


class PositionItem(BaseModel):
    id: str
    market_id: str
    quantity: float
    entry_price: float
    current_price: float
    pnl: float
    realized_pnl: float
    updated_at: str


class PositionsResponse(BaseModel):
    trading_user_id: str | None
    positions: list[PositionItem]


class OrderItem(BaseModel):
    id: str
    symbol: str
    side: str
    quantity: float
    order_type: str
    status: str


class OrdersResponse(BaseModel):
    trading_user_id: str | None
    orders: list[OrderItem]


class TradeItem(BaseModel):
    id: str
    market_id: str
    side: str
    quantity: float
    price: float
    status: str
    filled_price: float | None
    filled_at: str | None
    external_order_id: str | None
    created_at: str


class TradesResponse(BaseModel):
    trading_user_id: str | None
    trades: list[TradeItem]


class KillSwitchResponse(BaseModel):
    engaged: bool
    reason: str
    circuit_open: bool
    consecutive_failures: int
    daily_realized_pnl: float


class ReadinessResponse(BaseModel):
    ready: bool
    checks: dict[str, object]


class StrategyItem(BaseModel):
    name: str
    template: str | None
    params: dict | None
    status: str
    version: str
    created_at: str


class StrategiesResponse(BaseModel):
    strategies: list[StrategyItem]


class EventTokenResponse(BaseModel):
    token: str
    expires_at: int

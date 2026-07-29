from __future__ import annotations

import uuid
from typing import Any

from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.adapters.broker_adapter import FillResult
from traderos.domain.exceptions import InfrastructureError
from traderos.domain.exceptions import ServiceError
from traderos.infrastructure.retry import retry_with_backoff

_has_alpaca: bool
try:
    from alpaca.trading.client import TradingClient as _TradingClient
    from alpaca.trading.requests import MarketOrderRequest

    _has_alpaca = True
    _OrderSide_BUY = "buy"
    _OrderSide_SELL = "sell"
    _TimeInForce_DAY = "day"
except ImportError:
    _has_alpaca = False
    _TradingClient = None  # type: ignore[assignment]
    MarketOrderRequest = None  # type: ignore[assignment]
    _OrderSide_BUY = "buy"
    _OrderSide_SELL = "sell"
    _TimeInForce_DAY = "day"


class AlpacaBrokerAdapter(BrokerAdapter):
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        paper: bool = True,
        symbol_map: dict[uuid.UUID, str] | None = None,
    ) -> None:
        if not _has_alpaca or _TradingClient is None:
            raise ImportError("alpaca-py is required. Install with: pip install alpaca-py")
        self._client: Any = _TradingClient(api_key, secret_key, paper=paper)
        self._symbol_map: dict[uuid.UUID, str] = symbol_map or {}

    def place_market_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        close_price: float | None = None,
    ) -> FillResult:
        try:
            if MarketOrderRequest is None:
                raise ImportError("alpaca-py not available")
            symbol = self._symbol_map.get(market_id, str(market_id))

            def _submit() -> Any:
                client = self._client
                req_cls = MarketOrderRequest
                if client is None:
                    raise InfrastructureError("Alpaca client not initialized")
                if req_cls is None:
                    raise InfrastructureError("alpaca-py MarketOrderRequest not available")
                return client.submit_order(
                    order_data=req_cls(
                        symbol=symbol,
                        qty=quantity,
                        side=_OrderSide_BUY if side == "buy" else _OrderSide_SELL,
                        time_in_force=_TimeInForce_DAY,
                    )
                )

            order = retry_with_backoff(_submit, max_retries=2)
            return FillResult(
                filled=True,
                fill_quantity=float(order.filled_qty or quantity),
                fill_price=float(order.filled_avg_price or 0.0),
                remaining=float(order.qty - (order.filled_qty or 0)),
                status="filled",
                order_id=order.id,
            )
        except (ValueError, RuntimeError, OSError, InfrastructureError, ServiceError) as e:
            return FillResult(False, 0.0, 0.0, quantity, "rejected", str(e))

    def place_limit_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        price: float,
        close_price: float | None = None,
    ) -> FillResult:
        try:
            from alpaca.trading.enums import OrderSide
            from alpaca.trading.enums import TimeInForce
            from alpaca.trading.requests import LimitOrderRequest

            symbol = self._symbol_map.get(market_id, str(market_id))

            def _submit() -> Any:
                client = self._client
                if client is None:
                    raise InfrastructureError("Alpaca client not initialized")
                return client.submit_order(
                    order_data=LimitOrderRequest(
                        symbol=symbol,
                        qty=quantity,
                        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                        time_in_force=TimeInForce.DAY,
                        limit_price=round(price, 2),
                    )
                )

            order = retry_with_backoff(_submit, max_retries=2)
            return FillResult(
                filled=bool(order.filled_qty),
                fill_quantity=float(order.filled_qty or 0),
                fill_price=float(order.filled_avg_price or price),
                remaining=float(order.qty - (order.filled_qty or 0)),
                status="filled" if order.filled_qty == order.qty else "pending",
                order_id=order.id,
            )
        except (ValueError, RuntimeError, OSError, InfrastructureError, ServiceError) as e:
            return FillResult(False, 0.0, 0.0, quantity, "rejected", str(e))

    def cancel_order(self, order_id: str) -> FillResult:
        try:
            self._client.cancel_order_by_id(order_id)
            return FillResult(True, 0.0, 0.0, 0.0, "cancelled", order_id)
        except (ValueError, RuntimeError, OSError, InfrastructureError, ServiceError) as e:
            return FillResult(False, 0.0, 0.0, 0.0, "rejected", str(e))

    def get_account_balance(self) -> float:
        account = self._client.get_account()
        return float(account.equity)

    def get_positions(self) -> list[dict]:
        positions = self._client.get_all_positions()
        return [
            {"symbol": p.symbol, "qty": float(p.qty), "market_value": float(p.market_value)}
            for p in positions
        ]

    def get_open_orders(self) -> list[dict]:
        orders = self._client.get_orders(status="open")
        return [
            {"id": str(o.id), "symbol": o.symbol, "qty": float(o.qty), "side": o.side, "type": o.type}
            for o in orders
        ]

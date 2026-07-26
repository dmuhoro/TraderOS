from __future__ import annotations

import uuid
from typing import Any

from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.adapters.broker_adapter import FillResult

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
    def __init__(self, api_key: str, secret_key: str, paper: bool = True) -> None:
        if not _has_alpaca:
            raise ImportError(
                "alpaca-py is required. Install with: pip install alpaca-py"
            )
        assert _TradingClient is not None
        self._client: Any = _TradingClient(api_key, secret_key, paper=paper)

    def place_market_order(
        self, market_id: uuid.UUID, side: str, quantity: float,
    ) -> FillResult:
        try:
            assert MarketOrderRequest is not None
            order = self._client.submit_order(
                order_data=MarketOrderRequest(
                    symbol=str(market_id),
                    qty=quantity,
                    side=_OrderSide_BUY if side == "buy" else _OrderSide_SELL,
                    time_in_force=_TimeInForce_DAY,
                )
            )
            return FillResult(
                filled=True,
                fill_quantity=float(order.filled_qty or quantity),
                fill_price=float(order.filled_avg_price or 0.0),
                remaining=float(order.qty - (order.filled_qty or 0)),
                status="filled",
                order_id=order.id,
            )
        except (ValueError, RuntimeError, OSError) as e:
            return FillResult(False, 0.0, 0.0, quantity, "rejected", str(e))

    def place_limit_order(
        self, market_id: uuid.UUID, side: str,
        quantity: float, price: float,
    ) -> FillResult:
        return FillResult(False, 0.0, 0.0, quantity, "pending", "")

    def cancel_order(self, order_id: str) -> FillResult:
        try:
            self._client.cancel_order_by_id(order_id)
            return FillResult(True, 0.0, 0.0, 0.0, "cancelled", order_id)
        except (ValueError, RuntimeError, OSError) as e:
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

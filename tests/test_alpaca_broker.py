from __future__ import annotations

import importlib
import uuid
from types import ModuleType
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from traderos.infrastructure import alpaca_broker
from traderos.infrastructure.alpaca_broker import AlpacaBrokerAdapter


class FakeOrder:
    def __init__(self, id="ord1", filled_qty=None, qty=1.0, filled_avg_price=None):
        self.id = id
        self.filled_qty = filled_qty
        self.qty = qty
        self.filled_avg_price = filled_avg_price


class FakeAccount:
    def __init__(self, equity="10000.0"):
        self.equity = equity


class FakePosition:
    def __init__(self, symbol="BTCUSD", qty="1.0", market_value="50000.0"):
        self.symbol = symbol
        self.qty = qty
        self.market_value = market_value


def _build_mock_alpaca():
    alpaca = ModuleType("alpaca")
    trading = ModuleType("alpaca.trading")
    enums = ModuleType("alpaca.trading.enums")
    requests = ModuleType("alpaca.trading.requests")
    client = ModuleType("alpaca.trading.client")

    class FakeOrderSide:
        BUY = "buy"
        SELL = "sell"

    class FakeTimeInForce:
        DAY = "day"

    class FakeOrderType:
        STOP = "stop"
        TRAILING_STOP = "trailing_stop"

    class _FakeRequest:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class FakeLimitOrderRequest(_FakeRequest):
        pass

    class FakeMarketOrderRequest(_FakeRequest):
        pass

    class FakeStopOrderRequest(_FakeRequest):
        pass

    class FakeTrailingStopOrderRequest(_FakeRequest):
        pass

    class FakeReplaceOrderRequest(_FakeRequest):
        pass

    enums.OrderSide = FakeOrderSide
    enums.TimeInForce = FakeTimeInForce
    enums.OrderType = FakeOrderType
    requests.LimitOrderRequest = FakeLimitOrderRequest
    requests.MarketOrderRequest = FakeMarketOrderRequest
    requests.StopOrderRequest = FakeStopOrderRequest
    requests.TrailingStopOrderRequest = FakeTrailingStopOrderRequest
    requests.ReplaceOrderRequest = FakeReplaceOrderRequest

    client.TradingClient = MagicMock()

    trading.enums = enums
    trading.requests = requests
    trading.client = client
    alpaca.trading = trading

    return alpaca, trading, client


@pytest.fixture(autouse=True)
def _patch_alpaca():
    alpaca, trading, client_mod = _build_mock_alpaca()
    real_client = client_mod.TradingClient.return_value
    real_client.get_account.return_value = FakeAccount()

    with patch.dict(
        "sys.modules",
        {
            "alpaca": alpaca,
            "alpaca.trading": trading,
            "alpaca.trading.enums": trading.enums,
            "alpaca.trading.requests": trading.requests,
            "alpaca.trading.client": trading.client,
        },
        clear=False,
    ):
        importlib.reload(alpaca_broker)
        yield real_client

    importlib.reload(alpaca_broker)


class TestAlpacaBrokerAdapter:
    def _make(self, client):
        return AlpacaBrokerAdapter(api_key="test", secret_key="test", paper=True)

    def test_import_error_when_no_alpaca(self):
        with (
            patch("traderos.infrastructure.alpaca_broker._has_alpaca", False),
            pytest.raises(ImportError, match="alpaca-py is required"),
        ):
            AlpacaBrokerAdapter(api_key="x", secret_key="y")

    def test_place_market_order_filled(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        _patch_alpaca.submit_order.return_value = FakeOrder(
            id="ord1", filled_qty=1.0, qty=1.0, filled_avg_price="50100.0"
        )
        result = adapter.place_market_order(uuid.uuid4(), "buy", 1.0)
        assert result.filled is True
        assert result.fill_quantity == 1.0
        assert result.fill_price == 50100.0

    def test_place_market_order_partial_fill(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        _patch_alpaca.submit_order.return_value = FakeOrder(
            id="ord1", filled_qty=0.5, qty=1.0, filled_avg_price="50000.0"
        )
        result = adapter.place_market_order(uuid.uuid4(), "sell", 1.0)
        assert result.filled is True
        assert result.fill_quantity == 0.5
        assert result.remaining == 0.5

    def test_place_market_order_rejected(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        _patch_alpaca.submit_order.side_effect = RuntimeError("insufficient funds")
        result = adapter.place_market_order(uuid.uuid4(), "buy", 1.0)
        assert result.filled is False
        assert result.status == "rejected"

    def test_place_limit_order_filled(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        _patch_alpaca.submit_order.return_value = FakeOrder(
            id="ord2", filled_qty=1.0, qty=1.0, filled_avg_price="50000.0"
        )
        result = adapter.place_limit_order(uuid.uuid4(), "buy", 1.0, 50000.0)
        assert result.filled is True
        assert result.fill_price == 50000.0

    def test_place_limit_order_pending(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        _patch_alpaca.submit_order.return_value = FakeOrder(
            id="ord2", filled_qty=0, qty=1.0, filled_avg_price=None
        )
        result = adapter.place_limit_order(uuid.uuid4(), "buy", 1.0, 50000.0)
        assert result.filled is False
        assert result.status == "pending"

    def test_place_limit_order_rejected(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        _patch_alpaca.submit_order.side_effect = RuntimeError("bad request")
        result = adapter.place_limit_order(uuid.uuid4(), "buy", 1.0, 50000.0)
        assert result.filled is False
        assert result.status == "rejected"

    def test_cancel_order(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        result = adapter.cancel_order("ord1")
        assert result.filled is True
        _patch_alpaca.cancel_order_by_id.assert_called_once_with("ord1")

    def test_cancel_order_rejected(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        _patch_alpaca.cancel_order_by_id.side_effect = RuntimeError("not found")
        result = adapter.cancel_order("ord1")
        assert result.filled is False

    def test_get_account_balance(self, _patch_alpaca):
        _patch_alpaca.get_account.return_value = FakeAccount(equity="25000.0")
        adapter = self._make(_patch_alpaca)
        assert adapter.get_account_balance() == 25000.0

    def test_get_positions(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        _patch_alpaca.get_all_positions.return_value = [
            FakePosition(symbol="BTCUSD", qty="1.5", market_value="75000.0")
        ]
        positions = adapter.get_positions()
        assert len(positions) == 1
        assert positions[0]["symbol"] == "BTCUSD"
        assert positions[0]["qty"] == 1.5

    def test_symbol_map_lookup(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        mid = uuid.uuid4()
        adapter._symbol_map = {mid: "ETHUSD"}
        _patch_alpaca.submit_order.return_value = FakeOrder(id="ord1", filled_qty=1.0, qty=1.0)
        adapter.place_market_order(mid, "buy", 1.0)
        order_data = _patch_alpaca.submit_order.call_args[1]["order_data"]
        assert order_data.symbol == "ETHUSD"

    def test_place_stop_order(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        _patch_alpaca.submit_order.return_value = FakeOrder(
            id="ord-stop", filled_qty=0, qty=1.0, filled_avg_price=None
        )
        result = adapter.place_stop_order(uuid.uuid4(), "sell", 1.0, 48000.0)
        assert result.filled is False
        assert result.status == "pending"
        order_data = _patch_alpaca.submit_order.call_args[1]["order_data"]
        assert order_data.stop_price == 48000.0
        assert order_data.type == "stop"

    def test_place_stop_order_rejected(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        _patch_alpaca.submit_order.side_effect = RuntimeError("bad stop")
        result = adapter.place_stop_order(uuid.uuid4(), "sell", 1.0, 48000.0)
        assert result.filled is False
        assert result.status == "rejected"

    def test_place_trailing_stop_order(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        _patch_alpaca.submit_order.return_value = FakeOrder(
            id="ord-trail", filled_qty=0, qty=1.0, filled_avg_price=None
        )
        result = adapter.place_trailing_stop_order(uuid.uuid4(), "sell", 1.0, 0.01)
        assert result.filled is False
        assert result.status == "pending"
        order_data = _patch_alpaca.submit_order.call_args[1]["order_data"]
        assert order_data.trail_percent == 0.01
        assert order_data.type == "trailing_stop"

    def test_modify_order(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        result = adapter.modify_order("ord1", qty=2.0, stop_price=47000.0)
        assert result.filled is True
        assert result.status == "modified"
        _patch_alpaca.replace_order_by_id.assert_called_once()
        order_data = _patch_alpaca.replace_order_by_id.call_args[1]["order_data"]
        assert order_data.qty == 2
        assert order_data.stop_price == 47000.0

    def test_modify_order_rejected(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        _patch_alpaca.replace_order_by_id.side_effect = RuntimeError("not found")
        result = adapter.modify_order("ord1", qty=2.0)
        assert result.filled is False
        assert result.status == "rejected"

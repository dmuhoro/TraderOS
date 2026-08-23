from __future__ import annotations

import importlib
import uuid
from types import ModuleType
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from traderos.domain.exceptions import InfrastructureError
from traderos.domain.exceptions import ServiceError
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
    common = ModuleType("alpaca.common")
    exceptions = ModuleType("alpaca.common.exceptions")

    class FakeAPIError(Exception):
        def __init__(self, message="broker error", status_code=503):
            super().__init__(message)
            self.status_code = status_code

    exceptions.APIError = FakeAPIError
    common.exceptions = exceptions
    alpaca.common = common

    class FakeOrderSide:
        BUY = "buy"
        SELL = "sell"

    class FakeQueryOrderStatus:
        OPEN = "open"

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

    class FakeGetOrdersRequest(_FakeRequest):
        pass

    enums.OrderSide = FakeOrderSide
    enums.QueryOrderStatus = FakeQueryOrderStatus
    enums.TimeInForce = FakeTimeInForce
    enums.OrderType = FakeOrderType
    requests.LimitOrderRequest = FakeLimitOrderRequest
    requests.MarketOrderRequest = FakeMarketOrderRequest
    requests.StopOrderRequest = FakeStopOrderRequest
    requests.TrailingStopOrderRequest = FakeTrailingStopOrderRequest
    requests.ReplaceOrderRequest = FakeReplaceOrderRequest
    requests.GetOrdersRequest = FakeGetOrdersRequest

    client.TradingClient = MagicMock()

    trading.enums = enums
    trading.requests = requests
    trading.client = client
    alpaca.trading = trading

    return alpaca, trading, client, common, exceptions


@pytest.fixture(autouse=True)
def _patch_alpaca():
    alpaca, trading, client_mod, common, exceptions = _build_mock_alpaca()
    real_client = client_mod.TradingClient.return_value
    real_client.get_account.return_value = FakeAccount()

    with patch.dict(
        "sys.modules",
        {
            "alpaca": alpaca,
            "alpaca.common": common,
            "alpaca.common.exceptions": exceptions,
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

    def test_get_open_orders_import_error_when_requests_missing(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        with (
            patch("traderos.infrastructure.alpaca_broker._GetOrdersRequest", None),
            patch("traderos.infrastructure.alpaca_broker._QueryOrderStatus", None),
            pytest.raises(ImportError, match="alpaca-py is required"),
        ):
            adapter.get_open_orders()

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

    def test_module_reload_without_alpaca_sets_guard_false(self):
        import sys

        keys = [k for k in list(sys.modules) if k.startswith("alpaca")]
        with patch.dict("sys.modules", {k: None for k in keys}):
            importlib.reload(alpaca_broker)
            assert alpaca_broker._has_alpaca is False
            assert alpaca_broker._TradingClient is None
            assert alpaca_broker._MarketOrderRequest is None
            assert alpaca_broker._ReplaceOrderRequest is None
            assert alpaca_broker._StopOrderRequest is None
            assert alpaca_broker._TrailingStopOrderRequest is None
            assert alpaca_broker._OrderSide is None
            assert alpaca_broker._OrderType is None
            assert alpaca_broker._TimeInForce is None
            # Without alpaca, nothing is ever classified as a transient API
            # error and the broker-error tuple omits APIError entirely.
            assert alpaca_broker._is_transient_api_error(RuntimeError("x")) is False
            err_types = alpaca_broker._broker_error_types()
            assert err_types == (
                ValueError,
                RuntimeError,
                OSError,
                InfrastructureError,
                ServiceError,
            )
        importlib.reload(alpaca_broker)

    def test_side_raises_when_enum_missing(self, _patch_alpaca):
        from traderos.domain.exceptions import InfrastructureError

        adapter = self._make(_patch_alpaca)
        with (
            patch.object(alpaca_broker, "_OrderSide", None),
            pytest.raises(InfrastructureError, match="OrderSide"),
        ):
            adapter._side("buy")

    def test_time_in_force_raises_when_enum_missing(self, _patch_alpaca):
        from traderos.domain.exceptions import InfrastructureError

        adapter = self._make(_patch_alpaca)
        with (
            patch.object(alpaca_broker, "_TimeInForce", None),
            pytest.raises(InfrastructureError, match="TimeInForce"),
        ):
            adapter._time_in_force()

    def test_place_market_order_client_none(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        adapter._client = None
        result = adapter.place_market_order(uuid.uuid4(), "buy", 1.0)
        assert result.filled is False
        assert result.status == "rejected"

    def test_place_market_order_request_none(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        with patch.object(alpaca_broker, "_MarketOrderRequest", None):
            result = adapter.place_market_order(uuid.uuid4(), "buy", 1.0)
        assert result.filled is False
        assert result.status == "rejected"

    def test_place_limit_order_client_none(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        adapter._client = None
        result = adapter.place_limit_order(uuid.uuid4(), "buy", 1.0, 50000.0)
        assert result.filled is False
        assert result.status == "rejected"

    def test_place_stop_order_client_none(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        adapter._client = None
        result = adapter.place_stop_order(uuid.uuid4(), "sell", 1.0, 48000.0)
        assert result.filled is False
        assert result.status == "rejected"

    def test_place_stop_order_request_none(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        with patch.object(alpaca_broker, "_StopOrderRequest", None):
            result = adapter.place_stop_order(uuid.uuid4(), "sell", 1.0, 48000.0)
        assert result.filled is False
        assert result.status == "rejected"

    def test_place_trailing_stop_client_none(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        adapter._client = None
        result = adapter.place_trailing_stop_order(uuid.uuid4(), "sell", 1.0, 0.01)
        assert result.filled is False
        assert result.status == "rejected"

    def test_place_trailing_stop_request_none(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        with patch.object(alpaca_broker, "_TrailingStopOrderRequest", None):
            result = adapter.place_trailing_stop_order(uuid.uuid4(), "sell", 1.0, 0.01)
        assert result.filled is False
        assert result.status == "rejected"

    def test_place_trailing_stop_rejected(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        _patch_alpaca.submit_order.side_effect = RuntimeError("bad trail")
        result = adapter.place_trailing_stop_order(uuid.uuid4(), "sell", 1.0, 0.01)
        assert result.filled is False
        assert result.status == "rejected"

    def test_modify_order_client_none(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        adapter._client = None
        result = adapter.modify_order("ord1", qty=2.0)
        assert result.filled is False
        assert result.status == "rejected"

    def test_modify_order_request_none(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        with patch.object(alpaca_broker, "_ReplaceOrderRequest", None):
            result = adapter.modify_order("ord1", qty=2.0)
        assert result.filled is False
        assert result.status == "rejected"

    def test_modify_order_limit_price(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        result = adapter.modify_order("ord1", limit_price=49900.0)
        assert result.filled is True
        assert result.status == "modified"
        order_data = _patch_alpaca.replace_order_by_id.call_args[1]["order_data"]
        assert order_data.limit_price == 49900.0

    def test_modify_order_trail(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        result = adapter.modify_order("ord1", trail_percent=0.02)
        assert result.filled is True
        assert result.status == "modified"
        order_data = _patch_alpaca.replace_order_by_id.call_args[1]["order_data"]
        assert order_data.trail == 0.02

    def test_modify_order_no_fields(self, _patch_alpaca):
        adapter = self._make(_patch_alpaca)
        result = adapter.modify_order("ord1")
        assert result.filled is False
        assert result.status == "rejected"

    def test_get_open_orders(self, _patch_alpaca):
        class _FakeOpenOrder:
            def __init__(self, id, symbol, qty, side, type):
                self.id = id
                self.symbol = symbol
                self.qty = qty
                self.side = side
                self.type = type

        _patch_alpaca.get_orders.return_value = [
            _FakeOpenOrder("o1", "BTCUSD", "1.0", "buy", "market")
        ]
        adapter = self._make(_patch_alpaca)
        orders = adapter.get_open_orders()
        assert len(orders) == 1
        assert orders[0]["symbol"] == "BTCUSD"
        assert orders[0]["side"] == "buy"
        called = _patch_alpaca.get_orders.call_args[0][0]
        assert called.status == "open"


class TestAlpacaAPIErrorHandling:
    """Alpaca's ``APIError`` is a bare ``Exception`` subclass: it must never
    escape the adapter (crashes the reconcile/cycle path — the batch-005 soak
    finding). Transient status codes (429/5xx) retry; permanent 4xx rejections
    become a clean rejected ``FillResult`` on submit paths and a ``ServiceError``
    on read paths."""

    def _make(self, client):
        return AlpacaBrokerAdapter(api_key="test", secret_key="test", paper=True)

    def test_transient_503_on_submit_retries_then_rejects_cleanly(self, _patch_alpaca):
        api_error_cls = alpaca_broker._AlpacaAPIError
        _patch_alpaca.submit_order.side_effect = [
            api_error_cls("service temporary unavailable", status_code=503),
            api_error_cls("service temporary unavailable", status_code=503),
            FakeOrder(id="ord", filled_qty="1.0", qty="1.0", filled_avg_price="50000.0"),
        ]
        adapter = self._make(_patch_alpaca)
        result = adapter.place_market_order(uuid.uuid4(), "buy", 1.0)
        assert result.filled is True
        assert _patch_alpaca.submit_order.call_count == 3

    def test_transient_503_exhausts_then_clean_reject(self, _patch_alpaca):
        api_error_cls = alpaca_broker._AlpacaAPIError
        _patch_alpaca.submit_order.side_effect = api_error_cls(
            "service temporary unavailable", status_code=503
        )
        adapter = self._make(_patch_alpaca)
        result = adapter.place_market_order(uuid.uuid4(), "buy", 1.0)
        assert result.filled is False
        assert result.status == "rejected"
        assert "failed after" in result.order_id  # the reason rides in order_id

    def test_permanent_400_on_submit_is_clean_reject_not_crash(self, _patch_alpaca):
        api_error_cls = alpaca_broker._AlpacaAPIError
        _patch_alpaca.submit_order.side_effect = api_error_cls(
            "order rejected by broker", status_code=400
        )
        adapter = self._make(_patch_alpaca)
        result = adapter.place_market_order(uuid.uuid4(), "buy", 1.0)
        assert result.filled is False
        assert result.status == "rejected"
        # Permanent 4xx must NOT burn retries behind a backoff.
        assert _patch_alpaca.submit_order.call_count == 1

    def test_transient_503_on_get_open_orders_raises_service_error(self, _patch_alpaca):
        api_error_cls = alpaca_broker._AlpacaAPIError
        _patch_alpaca.get_orders.side_effect = api_error_cls(
            "service temporary unavailable", status_code=503
        )
        adapter = self._make(_patch_alpaca)
        with pytest.raises(ServiceError, match="Failed to fetch open orders"):
            adapter.get_open_orders()

    def test_transient_503_on_get_positions_raises_service_error(self, _patch_alpaca):
        api_error_cls = alpaca_broker._AlpacaAPIError
        _patch_alpaca.get_all_positions.side_effect = api_error_cls(
            "service temporary unavailable", status_code=503
        )
        adapter = self._make(_patch_alpaca)
        with pytest.raises(ServiceError, match="Failed to fetch positions"):
            adapter.get_positions()

    def test_transient_503_on_get_account_raises_service_error(self, _patch_alpaca):
        api_error_cls = alpaca_broker._AlpacaAPIError
        _patch_alpaca.get_account.side_effect = api_error_cls(
            "service temporary unavailable", status_code=503
        )
        adapter = self._make(_patch_alpaca)
        with pytest.raises(ServiceError, match="Failed to fetch account balance"):
            adapter.get_account_balance()

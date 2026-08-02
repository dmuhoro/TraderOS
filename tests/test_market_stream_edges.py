from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from traderos.infrastructure import market_stream as ms
from traderos.infrastructure.market_stream import BinanceStreamTransport
from traderos.infrastructure.market_stream import CandleAggregator
from traderos.infrastructure.market_stream import InvalidTickError
from traderos.infrastructure.market_stream import StreamingMarketDataService
from traderos.infrastructure.market_stream import Tick
from traderos.infrastructure.market_stream import _DefaultWebSocketConnector
from traderos.infrastructure.market_stream import normalize_timestamp
from traderos.infrastructure.market_stream import parse_trade_frame
from traderos.infrastructure.market_stream import validate_tick


class _Transport:
    def close(self) -> None:
        pass


def _raw_tick(**overrides) -> dict:
    base = {
        "symbol": "BTCUSDT",
        "price": "100",
        "quantity": "1",
        "timestamp": datetime.now(tz=UTC).timestamp(),
    }
    base.update(overrides)
    return base


def _tick_at(symbol: str, seconds_offset: int, price: str = "10") -> Tick:
    base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC) + timedelta(seconds=seconds_offset)
    return Tick(symbol, Decimal(price), Decimal(1), base, base)


class TestNormalizeTimestampEdges:
    @pytest.mark.parametrize("value", ["abc", None, object()])
    def test_invalid_value_raises(self, value) -> None:
        with pytest.raises(InvalidTickError, match="invalid timestamp"):
            normalize_timestamp(value)

    def test_non_finite_raises(self) -> None:
        with pytest.raises(InvalidTickError):
            normalize_timestamp(float("nan"))
        with pytest.raises(InvalidTickError):
            normalize_timestamp(float("inf"))

    def test_overflow_raises(self) -> None:
        with pytest.raises(InvalidTickError):
            normalize_timestamp(1e30)


class TestValidateTickEdges:
    def test_missing_price_raises(self) -> None:
        raw = _raw_tick()
        del raw["price"]
        with pytest.raises(InvalidTickError, match="invalid price"):
            validate_tick(raw)

    def test_non_numeric_price_raises(self) -> None:
        with pytest.raises(InvalidTickError, match="invalid price"):
            validate_tick(_raw_tick(price="abc"))

    def test_non_numeric_quantity_raises(self) -> None:
        with pytest.raises(InvalidTickError, match="invalid quantity"):
            validate_tick(_raw_tick(quantity="abc"))


class TestParseTradeFrameEdges:
    def test_missing_or_empty_symbol_returns_none(self) -> None:
        assert parse_trade_frame('{"e":"aggTrade","p":"50000","q":"1"}') is None
        assert parse_trade_frame('{"e":"aggTrade","p":"50000","q":"1","s":""}') is None


class TestDefaultWebSocketConnector:
    def test_raises_when_websockets_missing(self) -> None:
        with (
            patch.dict("sys.modules", {"websockets": None}),
            pytest.raises(RuntimeError, match="websockets"),
        ):
            _DefaultWebSocketConnector()("wss://stream.binance.com:9443")


class TestBinanceStreamTransportClose:
    def test_close_survives_ws_error(self) -> None:
        ws = MagicMock()
        ws.recv.return_value = None
        ws.close.side_effect = RuntimeError("connection reset")
        transport = BinanceStreamTransport(connector=lambda url: ws)
        assert list(transport.connect(["BTCUSDT"])) == []
        transport.close()

    def test_close_idempotent(self) -> None:
        transport = BinanceStreamTransport(connector=lambda url: MagicMock())
        transport.close()


class TestCandleAggregatorRollover:
    def test_late_tick_after_rollover_is_counted(self) -> None:
        agg = CandleAggregator(60)
        agg.add(_tick_at("BTC", 10))
        agg.add(_tick_at("BTC", 70))
        late = agg.add(_tick_at("BTC", 50))
        assert late is None
        assert agg.late_ticks == 1


class TestStreamingServiceControl:
    def test_stop_closes_transport(self) -> None:
        class TrackedTransport(_Transport):
            def __init__(self) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        transport = TrackedTransport()
        service = StreamingMarketDataService(transport)
        service.start()
        service.stop()
        assert service._running is False
        assert transport.closed is True

    def test_run_breaks_when_stopped_from_handler(self) -> None:
        class LoopTransport(_Transport):
            def connect(self, symbols):
                while True:
                    yield _raw_tick()

        service = StreamingMarketDataService(LoopTransport())
        service.subscribe(["BTCUSDT"], lambda tick: service.stop())
        received = service.run(max_messages=10)
        assert received == 1
        assert service._running is False


class TestStreamingServiceReconnect:
    def test_run_reconnects_and_recovers(self) -> None:
        class FlakyTransport(_Transport):
            def __init__(self) -> None:
                self.calls = 0

            def connect(self, symbols):
                self.calls += 1
                if self.calls == 1:
                    raise ConnectionError("transient")
                yield _raw_tick()

        service = StreamingMarketDataService(FlakyTransport(), reconnect_limit=3)
        with patch.object(ms.time, "sleep") as sleep:
            received = service.run(max_messages=1)
        assert received == 1
        sleep.assert_called_once()

    def test_run_gives_up_after_reconnect_limit(self) -> None:
        class AlwaysDownTransport(_Transport):
            def connect(self, symbols):
                raise ConnectionError("down")

        service = StreamingMarketDataService(AlwaysDownTransport(), reconnect_limit=1)
        with patch.object(ms.time, "sleep") as sleep:
            received = service.run()
        assert received == 0
        assert service._running is False
        assert sleep.call_count == 1


class TestStreamingServiceHealth:
    def test_ingest_drops_when_queue_full(self) -> None:
        service = StreamingMarketDataService(_Transport(), max_queue=1)
        service.ingest(_raw_tick())
        service.ingest(_raw_tick())
        assert service.dropped_ticks == 1
        assert service._queue.qsize() == 1

    def test_health_all_states(self) -> None:
        service = StreamingMarketDataService(_Transport())
        status = service.health()
        assert status.healthy is False
        assert status.latency_ms == 0.0

        service.start()
        assert service.health().healthy is True

        service.ingest(_raw_tick())
        status = service.health()
        assert status.healthy is True
        assert status.latency_ms >= 0.0

        service._last_tick = datetime(2020, 1, 1, tzinfo=UTC)
        status = service.health()
        assert status.healthy is False
        assert "heartbeat" in status.message

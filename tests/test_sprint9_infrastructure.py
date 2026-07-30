import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

import pytest

from traderos.application.order_event_engine import OrderEventEngine
from traderos.domain.entities.trade import InvalidTradeTransitionError
from traderos.domain.entities.trade import Trade
from traderos.domain.entities.trade import TradeSide
from traderos.domain.entities.trade import TradeStatus
from traderos.infrastructure.events import InMemoryEventBus
from traderos.infrastructure.market_stream import CandleAggregator
from traderos.infrastructure.market_stream import ClockMonitor
from traderos.infrastructure.market_stream import StreamingMarketDataService
from traderos.infrastructure.market_stream import Tick


class Transport:
    def close(self):
        pass


def test_stream_latency_backpressure_and_replay():
    service = StreamingMarketDataService(Transport(), max_queue=1)
    service.start()
    now = datetime.now(tz=UTC)
    service.ingest(
        {"symbol": "BTCUSDT", "price": "100", "quantity": "1", "timestamp": now.timestamp()}
    )
    service.ingest(
        {"symbol": "BTCUSDT", "price": "101", "quantity": "1", "timestamp": now.timestamp()}
    )
    assert service.dropped_ticks == 1
    assert len(service.recorder.replay()) == 2


def test_aggregator_emits_closed_candle():
    agg = CandleAggregator(60)
    t = datetime(2026, 1, 1, 0, 0, 10, tzinfo=UTC)
    assert agg.add(Tick("BTC", Decimal("10"), Decimal("2"), t, t)) is None
    candle = agg.add(
        Tick(
            "BTC", Decimal("12"), Decimal("3"), t + timedelta(seconds=60), t + timedelta(seconds=60)
        )
    )
    assert candle and candle.open == Decimal("10") and candle.close == Decimal("10")


def test_clock_drift_is_detected():
    monitor = ClockMonitor(100)
    now = datetime.now(tz=UTC)
    assert not monitor.observe(now - timedelta(seconds=1), now)


def test_order_engine_is_idempotent_and_emits():
    trade = Trade(uuid.uuid4(), uuid.uuid4(), TradeSide.BUY, 2, 100)
    events = []
    bus = InMemoryEventBus()
    bus.subscribe("execution.order_status", events.append)
    persisted = []
    engine = OrderEventEngine(bus, persist=persisted.append)
    trade.submit("broker-1")
    assert engine.apply(trade, TradeStatus.ACKNOWLEDGED, event_id="ack-1")
    assert not engine.apply(trade, TradeStatus.ACKNOWLEDGED, event_id="ack-1")
    assert engine.apply(
        trade, TradeStatus.PARTIALLY_FILLED, event_id="fill-1", fill_quantity=1, fill_price=101
    )
    assert trade.status == TradeStatus.PARTIALLY_FILLED and len(events) == 2 and len(persisted) == 2
    with pytest.raises(InvalidTradeTransitionError):
        engine.apply(
            trade, TradeStatus.PARTIALLY_FILLED, event_id="late", fill_quantity=1, fill_price=100
        )

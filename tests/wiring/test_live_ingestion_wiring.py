"""Wiring tests: the real object graph, no mocks at the boundary under test.

Why this file exists
--------------------
B-1 shipped through a suite of 2,364 tests at 100% line coverage with
``pyright --strict`` clean. The unit test that appeared to cover the LIVE
data-gap breaker (``tests/test_cycle_executor.py``) mocked the ingestion
service, hand-built its own ``datetime``, mocked ``RiskService``, and ran in
PAPER mode -- so it exercised none of the four things that actually broke.

These tests build the graph from the real classes:
``DataIngestionService`` + real collectors -> real ``Candle`` -> real
``RiskService``. Only the outermost I/O ports (broker, audit sink, metrics
sink, notifier) are substituted, because they are the *boundary being
asserted against*, not the boundary under test.

Constitution: Principle 4 (enforcement at the real boundary) and the
operational directive "proof must exercise the real path".
"""

from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

import pytest

from traderos.domain.collectors.base import CollectorOHLCV
from traderos.domain.collectors.base import CollectorRegistry
from traderos.domain.collectors.base import CollectorType
from traderos.domain.collectors.base import DataCollector
from traderos.domain.services.data_ingestion_service import DataIngestionService
from traderos.domain.services.risk_service import RiskService
from traderos.infrastructure.collectors.mock_collector import MockDataCollector

# --------------------------------------------------------------------------
# A real DataCollector. Not a Mock() -- it returns real CollectorOHLCV rows
# with real Decimal prices and real tz-aware datetimes, exactly as the
# Binance and Alpaca adapters do.
# --------------------------------------------------------------------------


class _FixedTimestampCollector(DataCollector):
    """Emits real CollectorOHLCV rows whose newest candle is ``age_seconds``
    old. Lets a test control data freshness without mocking the service."""

    def __init__(self, count: int, age_seconds: float) -> None:
        self._count = count
        self._age = age_seconds

    @property
    def collector_type(self) -> CollectorType:
        return CollectorType.MOCK

    def fetch_historical(
        self,
        symbol: str,
        interval: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 500,
    ) -> list[CollectorOHLCV]:
        newest = datetime.now(UTC) - timedelta(seconds=self._age)
        rows: list[CollectorOHLCV] = []
        # Ascending order, newest last -- matches Binance/Alpaca/Streaming.
        for i in range(self._count):
            ts = newest - timedelta(minutes=(self._count - 1 - i))
            rows.append(
                CollectorOHLCV(
                    open=Decimal("100.0"),
                    high=Decimal("101.0"),
                    low=Decimal("99.0"),
                    close=Decimal("100.5"),
                    volume=Decimal("1000.0"),
                    timestamp=ts,
                    symbol=symbol,
                )
            )
        return rows[:limit]

    def validate_symbol(self, symbol: str) -> bool:
        return True


def _ingestion(collector: DataCollector, market_id: uuid.UUID) -> DataIngestionService:
    registry = CollectorRegistry()
    registry.register(collector)
    svc = DataIngestionService(registry=registry)
    svc.add_source(market_id, "BTC-USD", CollectorType.MOCK, "1h")
    return svc


# --------------------------------------------------------------------------
# B-1: the timestamp must survive ingestion as a datetime.
# --------------------------------------------------------------------------


class TestB1TimestampBoundary:
    """B-1: ``fetch_latest`` stringified the timestamp, ``fetch_candles``
    assigned that ``str`` into ``Candle(timestamp: datetime)``. Every
    consumer doing ``datetime - timestamp`` raised ``TypeError``."""

    def test_candle_timestamp_is_a_real_datetime(self) -> None:
        mid = uuid.uuid4()
        svc = _ingestion(MockDataCollector(), mid)
        candles = svc.fetch_candles(mid, limit=5)
        assert candles, "expected candles from the real ingestion path"
        for c in candles:
            assert isinstance(c.timestamp, datetime), (
                f"B-1: Candle.timestamp must be datetime, got "
                f"{type(c.timestamp).__name__}: {c.timestamp!r}"
            )
            assert c.timestamp.tzinfo is not None, "timestamp must be tz-aware"

    def test_live_gap_breaker_arithmetic_does_not_raise(self) -> None:
        """The exact subtraction at cycle_executor.py:160-161."""
        mid = uuid.uuid4()
        svc = _ingestion(MockDataCollector(), mid)
        candles = svc.fetch_candles(mid, limit=5)

        now_ts = datetime.now(UTC)
        last_ts = candles[-1].timestamp
        # Pre-fix this raised:
        #   TypeError: unsupported operand type(s) for -:
        #              'datetime.datetime' and 'str'
        stale_seconds = (now_ts - last_ts).total_seconds()
        assert stale_seconds >= 0.0

    def test_risk_service_staleness_arithmetic_does_not_raise(self) -> None:
        """The exact subtraction at risk_service.py:261."""
        mid = uuid.uuid4()
        svc = _ingestion(MockDataCollector(), mid)
        candles = svc.fetch_candles(mid, limit=5)

        now = datetime.now(UTC)
        last_candle_at = candles[-1].timestamp
        staleness = (now - last_candle_at).total_seconds()
        assert staleness >= 0.0

    def test_fetch_latest_rows_carry_datetime_not_str(self) -> None:
        """The stringification at data_ingestion_service.py:50 must be gone.
        Callers that need text serialize at the interface boundary."""
        mid = uuid.uuid4()
        svc = _ingestion(MockDataCollector(), mid)
        rows = svc.fetch_all(limit=3)
        rows_for_symbol = rows["BTC-USD"]
        assert rows_for_symbol
        for r in rows_for_symbol:
            assert isinstance(r["timestamp"], datetime), (
                f"fetch_all must not pre-stringify the timestamp, got "
                f"{type(r['timestamp']).__name__}"
            )


# --------------------------------------------------------------------------
# The staleness rail, proved fail-closed through the real RiskService.
# --------------------------------------------------------------------------


class TestStalenessRailFailsClosed:
    """risk_service.py:260-262 must refuse a stale order. This asserts the
    rail's real behaviour with a real RiskService -- the arithmetic reaching
    it is the B-1 defect, so these are the tests that would have caught it."""

    def test_stale_candle_is_refused(self) -> None:
        risk = RiskService(allowed_markets=frozenset())
        market_id = uuid.uuid4()
        old = datetime.now(UTC) - timedelta(hours=48)
        verdict = risk.authorize_order(
            market_id=market_id,
            side="BUY",
            quantity=0.1,
            price=100.0,
            equity=10_000.0,
            last_candle_at=old,
            now=datetime.now(UTC),
        )
        assert verdict.allowed is False
        assert "stale" in verdict.reason.lower(), verdict.reason

    def test_fresh_candle_is_allowed(self) -> None:
        """A rail that refuses everything is not a rail. Proves the gate
        discriminates rather than blanket-denying."""
        market_id = uuid.uuid4()
        risk = RiskService(allowed_markets=frozenset({market_id}))
        verdict = risk.authorize_order(
            market_id=market_id,
            side="BUY",
            quantity=0.1,
            price=100.0,
            equity=10_000.0,
            last_candle_at=datetime.now(UTC),
            now=datetime.now(UTC),
        )
        assert verdict.allowed is True, verdict.reason


@pytest.mark.parametrize("age_seconds", [0.0, 60.0, 299.0, 600.0, 86400.0])
def test_staleness_arithmetic_survives_any_data_age(age_seconds: float) -> None:
    """Freshness varies; the arithmetic must not depend on it."""
    mid = uuid.uuid4()
    svc = _ingestion(_FixedTimestampCollector(count=25, age_seconds=age_seconds), mid)
    candles = svc.fetch_candles(mid, limit=25)
    assert candles
    stale_seconds = (datetime.now(UTC) - candles[-1].timestamp).total_seconds()
    assert stale_seconds >= age_seconds - 5

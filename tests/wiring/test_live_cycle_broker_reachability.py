"""End-to-end LIVE-cycle proof for B-1, on the real object graph.

This is the test whose absence let B-1 ship through 2,364 tests at 100%
line coverage with ``pyright --strict`` clean.

What is real here
-----------------
* ``DataIngestionService`` with a real ``DataCollector``
* real ``Candle`` / ``OHLCV`` / ``datetime`` values flowing through
* real ``CycleExecutor.run()`` in ``TradingMode.LIVE``
* real ``AnalysisService`` indicator computation
* real ``RiskService`` fail-closed rails
* real ``PortfolioService``, ``ExecutionService``, ``HealthService``,
  ``MetricsService``, ``InMemoryEventBus``, ``RunManifestService``,
  ``AuditService``
* real SQLite repositories

What is substituted, and why that is legitimate
-----------------------------------------------
Only ``BrokerAdapter`` -- it is the *boundary being asserted against*.
The directive is "assert the broker is NEVER called when a check refuses
the order". A real exchange call cannot be made in a unit test, and
substituting it does not weaken the claim: the assertion is about whether
``place_market_order`` is *reached*, which is decided entirely by code
under test, not by the broker implementation.

The signal service is stubbed to always emit a LONG signal, guaranteeing
the executor always *wants* to trade. That makes the test adversarial:
if any rail is missing, the broker spy WILL record a call.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

import pytest

from traderos.application.cycle_executor import CycleExecutor
from traderos.application.models import TradingMode
from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.adapters.broker_adapter import FillResult
from traderos.domain.collectors.base import CollectorOHLCV
from traderos.domain.collectors.base import CollectorRegistry
from traderos.domain.collectors.base import CollectorType
from traderos.domain.collectors.base import DataCollector
from traderos.domain.entities.signal import SignalDirection
from traderos.domain.services.analysis_service import AnalysisService
from traderos.domain.services.data_ingestion_service import DataIngestionService
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.notification_service import NotificationService
from traderos.domain.services.portfolio_service import PortfolioService
from traderos.domain.services.signal_service import SignalProvenance
from traderos.infrastructure.audit import AuditService
from traderos.infrastructure.events import InMemoryEventBus
from traderos.infrastructure.health import HealthService
from traderos.infrastructure.metrics import MetricsService
from traderos.infrastructure.repositories.sqlite.trades import SQLitePositionRepository
from traderos.infrastructure.repositories.sqlite.trades import SQLiteTradeRepository
from traderos.infrastructure.run_manifest import RunManifestService


class _AgingCollector(DataCollector):
    """Real collector whose newest candle is ``age_seconds`` old."""

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
        for i in range(self._count):
            ts = newest - timedelta(minutes=(self._count - 1 - i))
            drift = Decimal("0.05") * i
            rows.append(
                CollectorOHLCV(
                    open=Decimal("100.0"),
                    high=Decimal("101.0") + drift,
                    low=Decimal("99.0"),
                    close=Decimal("100.5") + drift,
                    volume=Decimal("1000.0"),
                    timestamp=ts,
                    symbol=symbol,
                )
            )
        return rows[:limit]

    def validate_symbol(self, symbol: str) -> bool:
        return True


class _SpyBroker(BrokerAdapter):
    """Records every submission attempt. This is the assertion target."""

    def __init__(self) -> None:
        self.market_orders: list[tuple] = []
        self.flatten_orders: list[tuple] = []

    def place_market_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        close_price: float | None = None,
        client_order_id: str | None = None,
    ) -> FillResult:
        self.market_orders.append((market_id, side, quantity, close_price, client_order_id))
        return FillResult(
            filled=True,
            fill_quantity=quantity,
            fill_price=close_price or 100.0,
            remaining=0.0,
            status="filled",
            order_id=f"spy-{len(self.market_orders)}",
        )

    def place_flatten_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        close_price: float | None = None,
    ) -> FillResult:
        self.flatten_orders.append((market_id, side, quantity, close_price))
        return FillResult(
            filled=True,
            fill_quantity=quantity,
            fill_price=close_price or 100.0,
            remaining=0.0,
            status="filled",
            order_id=f"flat-{len(self.flatten_orders)}",
        )

    def get_account_balance(self) -> float:
        return 10_000.0

    # -- remaining BrokerAdapter surface -------------------------------
    # Not exercised by the cycle path; implemented so the spy is a concrete
    # adapter. None of these record, because the assertions are about market
    # order reachability specifically.

    def place_limit_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        price: float,
        close_price: float | None = None,
    ) -> FillResult:
        raise AssertionError("place_limit_order must not be reached in this test")

    def cancel_order(self, order_id: str) -> FillResult:
        raise AssertionError("cancel_order must not be reached in this test")

    def place_stop_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        stop_price: float,
        market_price: float | None = None,
    ) -> FillResult:
        raise AssertionError("place_stop_order must not be reached in this test")

    def place_trailing_stop_order(
        self,
        market_id: uuid.UUID,
        side: str,
        quantity: float,
        trail_percent: float,
        market_price: float | None = None,
    ) -> FillResult:
        raise AssertionError("place_trailing_stop_order must not be reached in this test")

    def modify_order(
        self,
        order_id: str,
        qty: float | None = None,
        limit_price: float | None = None,
        stop_price: float | None = None,
        trail_percent: float | None = None,
    ) -> FillResult:
        raise AssertionError("modify_order must not be reached in this test")

    def get_positions(self) -> list[dict]:
        return []

    def get_open_orders(self) -> list[dict]:
        return []


class _AlwaysLongSignalService:
    """Always emits a tradeable LONG signal so the executor always wants to
    trade. Makes the staleness test adversarial: a missing rail shows up as
    a recorded broker call."""

    def process_evaluation(self, *args: object, **kwargs: object) -> SignalProvenance | None:
        from traderos.domain.entities.signal import Signal

        now = datetime.now(UTC)
        signal = Signal(
            market_id=uuid.uuid4(),
            strategy_id=uuid.uuid4(),
            direction=SignalDirection.LONG,
            confidence=0.9,
            generated_at=now,
            expires_at=now + timedelta(hours=1),
        )
        return SignalProvenance(signal=signal, strategy_name="always_long", indicators_used={})


def _build_live_executor(
    age_seconds: float, max_staleness_seconds: float = 300.0
) -> tuple[CycleExecutor, _SpyBroker, HealthService, MetricsService, uuid.UUID]:
    market_id = uuid.uuid4()
    collector = _AgingCollector(count=60, age_seconds=age_seconds)

    registry = CollectorRegistry()
    registry.register(collector)
    ingestion = DataIngestionService(registry=registry)
    ingestion.add_source(market_id, "BTC-USD", CollectorType.MOCK, "1h")

    conn = sqlite3.connect(":memory:")
    trade_repo = SQLiteTradeRepository(conn)
    position_repo = SQLitePositionRepository(conn)

    audit = AuditService()
    metrics = MetricsService()
    health = HealthService()
    event_bus = InMemoryEventBus()

    from traderos.domain.services.risk_service import RiskService

    risk = RiskService(
        allowed_markets=frozenset({market_id}),
        max_data_staleness_seconds=max_staleness_seconds,
        audit=audit,
        metrics=metrics,
    )
    portfolio = PortfolioService(
        trade_repo=trade_repo, position_repo=position_repo, audit=audit, risk_service=risk
    )
    broker = _SpyBroker()

    executor = CycleExecutor(
        mode=TradingMode.LIVE,
        signal_service=_AlwaysLongSignalService(),  # type: ignore[arg-type]
        risk_service=risk,
        portfolio_service=portfolio,
        execution=ExecutionService(),
        analysis=AnalysisService(),
        broker=broker,
        event_bus=event_bus,
        health=health,
        audit=audit,
        metrics=metrics,
        notifications=NotificationService(),
        run_manifest=RunManifestService(),
        data_ingestion=ingestion,
        trading_user_id=None,
    )
    return executor, broker, health, metrics, market_id


class TestB1LiveCycleNoLongerRaisesTypeError:
    """The G-03 data-gap breaker runs at cycle_executor.py:155-167 in LIVE
    mode. Pre-fix this raised TypeError, which is NOT in _CYCLE_EXCEPTIONS,
    so it escaped run() entirely."""

    def test_stale_data_blocks_and_does_not_raise(self) -> None:
        executor, _broker, _health, _metrics, market_id = _build_live_executor(
            age_seconds=86_400.0, max_staleness_seconds=300.0
        )
        # Must not raise. Pre-fix: TypeError: unsupported operand type(s)
        # for -: 'datetime.datetime' and 'str'
        result = executor.run(market_id, close_price=100.5)

        assert result is not None
        assert any(
            "stale" in e for e in result.errors
        ), f"expected a staleness error, got {result.errors}"

    def test_stale_data_never_reaches_the_broker(self) -> None:
        """THE assertion: the broker must never be called on stale data."""
        executor, broker, _health, _metrics, market_id = _build_live_executor(age_seconds=86_400.0)
        executor.run(market_id, close_price=100.5)
        assert (
            broker.market_orders == []
        ), f"broker was called on stale data: {broker.market_orders}"

    def test_stale_data_records_the_data_gap_metric(self) -> None:
        executor, _broker, _health, metrics, market_id = _build_live_executor(age_seconds=86_400.0)
        executor.run(market_id, close_price=100.5)
        assert metrics.get_counter("risk.data_gap_blocked") > 0

    def test_fresh_data_reaches_the_pipeline(self) -> None:
        """Control case. A rail that blocks everything is not a rail.

        This does not assert that an order WAS placed -- sizing and other
        rails may legitimately refuse. It asserts the staleness rail did NOT
        block, proving it discriminates on data freshness rather than
        blanket-denying.
        """
        executor, _broker, _health, _metrics, market_id = _build_live_executor(
            age_seconds=1.0, max_staleness_seconds=300.0
        )
        result = executor.run(market_id, close_price=100.5)
        assert not any(
            "stale" in e for e in result.errors
        ), f"fresh data must not trip the staleness rail, got {result.errors}"


@pytest.mark.parametrize("age", [0.0, 299.0, 301.0, 3600.0, 604_800.0])
def test_live_cycle_survives_any_data_age(age: float) -> None:
    """The arithmetic must not depend on data age. Pre-fix every one of
    these raised TypeError regardless of age.

    Ages are chosen off the 300s threshold so the assertion is not racy:
    real elapsed time between building the collector and running the cycle
    pushes staleness a few hundredths past the requested age, which would
    make an exactly-at-threshold case indeterminate. The exact boundary
    semantics are pinned deterministically in
    ``test_staleness_threshold_is_strictly_greater_than``.
    """
    executor, broker, _health, _metrics, market_id = _build_live_executor(age_seconds=age)
    result = executor.run(market_id, close_price=100.5)
    if age > 300.0:
        assert any(
            "stale" in e for e in result.errors
        ), f"age {age}s must trip the staleness rail, got {result.errors}"
        assert broker.market_orders == [], "stale data must not reach the broker"
    else:
        assert not any(
            "stale" in e for e in result.errors
        ), f"age {age}s must not trip the staleness rail, got {result.errors}"


def test_staleness_threshold_is_strictly_greater_than() -> None:
    """Pins the boundary semantics with no wall-clock race.

    The gate is ``stale_seconds > threshold`` (cycle_executor.py:163), so
    data exactly at the threshold is still tradable. Verified against the
    real RiskService rail with an explicitly supplied ``now``.
    """
    from traderos.domain.services.risk_service import RiskService

    market_id = uuid.uuid4()
    threshold = 300.0
    now = datetime.now(UTC)

    at_threshold = RiskService(allowed_markets=frozenset({market_id})).authorize_order(
        market_id=market_id,
        side="BUY",
        quantity=0.1,
        price=100.0,
        equity=10_000.0,
        last_candle_at=now - timedelta(seconds=threshold),
        now=now,
    )
    assert at_threshold.allowed is True, at_threshold.reason

    just_past = RiskService(allowed_markets=frozenset({market_id})).authorize_order(
        market_id=market_id,
        side="BUY",
        quantity=0.1,
        price=100.0,
        equity=10_000.0,
        last_candle_at=now - timedelta(seconds=threshold + 1),
        now=now,
    )
    assert just_past.allowed is False
    assert "stale" in just_past.reason.lower(), just_past.reason

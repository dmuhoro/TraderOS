"""Win-rate provenance: no sizing may be computed from an invented win rate.

`RiskService.assess_trade` defaulted `win_rate=0.5`. Both production callers
omitted the argument, so every LIVE position size came out of a fabricated
coin-flip assumption dressed as a Kelly estimate. A default that silently
stands in for a measurement the system never made is a fabricated claim, and
the resulting `kelly_fraction` gates real order flow.

Unknown win rate must refuse, visibly:

- the refusal carries a reason (no silent drop);
- no order reaches the broker;
- the refusal is counted and audited;
- an explicitly supplied win rate still sizes normally.

Phase 3 replaces the estimator with a Bayesian/fractional-Kelly calculation
fed by measured performance. Until then the honest state is "unknown, so no
size".
"""

from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import Mock

from traderos.application.cycle_executor import CycleExecutor
from traderos.application.models import TradingMode
from traderos.domain.entities import Signal
from traderos.domain.entities import SignalDirection
from traderos.domain.entities.candle import Candle
from traderos.domain.entities.value_objects import OHLCV
from traderos.domain.services.analysis_service import AnalysisService
from traderos.domain.services.portfolio_service import PortfolioService
from traderos.domain.services.risk_service import KillSwitch
from traderos.domain.services.risk_service import RiskService
from traderos.domain.services.signal_service import SignalProvenance
from traderos.infrastructure.audit import AuditService as InMemoryAuditService
from traderos.infrastructure.events import InMemoryEventBus
from traderos.infrastructure.health import HealthService as InMemoryHealthService
from traderos.infrastructure.metrics import MetricsService as InMemoryMetricsService
from traderos.infrastructure.repositories.in_memory import InMemoryPositionRepository
from traderos.infrastructure.repositories.in_memory import InMemoryTradeRepository
from traderos.infrastructure.run_manifest import RunManifestService as InMemoryManifestService


def _provenance(direction: str = "long", confidence: float = 0.8) -> SignalProvenance:
    now = datetime.now(UTC)
    signal = Signal(
        market_id=uuid.uuid4(),
        strategy_id=uuid.uuid4(),
        direction=SignalDirection(direction),
        confidence=confidence,
        generated_at=now,
        expires_at=now + timedelta(hours=1),
    )
    return SignalProvenance(
        signal=signal,
        strategy_name="test-strategy",
        indicators_used={},
    )


def _candles(close: str = "100.0", high: str = "105.0", low: str = "95.0") -> list[Candle]:
    return [
        Candle(
            market_id=uuid.uuid4(),
            ohlcv=OHLCV(
                Decimal(close),
                Decimal(high),
                Decimal(low),
                Decimal(close),
                Decimal("1000.0"),
            ),
            # Fresh candles: a stale series is blocked upstream by the data-gap
            # breaker, which would stop the cycle before risk is ever consulted
            # and make this test pass without testing anything.
            timestamp=datetime.now(UTC) - timedelta(minutes=30 - i),
            timeframe="1m",
        )
        for i in range(30)
    ]


class _CountingBroker:
    """Innermost adapter. Any increment means an order escaped.

    Implements the full read side too, so the cycle actually reaches the
    order path instead of short-circuiting on a missing method.
    """

    def __init__(self) -> None:
        self.submits = 0

    def get_account_balance(self) -> float:
        return 10000.0

    def get_positions(self) -> list[dict]:
        return []

    def get_open_orders(self) -> list[dict]:
        return []

    def place_market_order(self, *_a: Any, **_k: Any) -> Any:
        self.submits += 1
        raise AssertionError("an order was placed from an unsourced win rate")

    def place_limit_order(self, *_a: Any, **_k: Any) -> Any:
        self.submits += 1
        raise AssertionError("an order was placed from an unsourced win rate")

    def place_flatten_order(self, *_a: Any, **_k: Any) -> Any:
        self.submits += 1
        raise AssertionError("an order was placed from an unsourced win rate")


def _executor(
    risk: RiskService, broker: _CountingBroker, metrics: Any, audit: Any
) -> CycleExecutor:
    portfolio = PortfolioService(InMemoryTradeRepository(), InMemoryPositionRepository())
    signal_service = Mock()
    signal_service.process_evaluation.return_value = _provenance()
    data_ingestion = Mock()
    data_ingestion.fetch_candles.return_value = _candles()
    return CycleExecutor(
        mode=TradingMode.LIVE,
        signal_service=signal_service,
        risk_service=risk,
        portfolio_service=portfolio,
        execution=Mock(),
        analysis=AnalysisService(),
        broker=broker,
        event_bus=InMemoryEventBus(),
        health=InMemoryHealthService(),
        audit=audit,
        metrics=metrics,
        notifications=Mock(),
        run_manifest=InMemoryManifestService(),
        data_ingestion=data_ingestion,
        default_cash=10000.0,
    )


def _live_risk() -> RiskService:
    risk = RiskService()
    risk.kill_switch = KillSwitch()
    return risk


class TestNoInventedWinRate:
    def test_assess_trade_refuses_without_a_sourced_win_rate(self) -> None:
        svc = _live_risk()
        result = svc.assess_trade(
            price=100.0,
            confidence=0.8,
            atr=5.0,
            account_equity=10000.0,
            win_rate=None,
        )
        assert result.kelly_fraction == 0.0, "no win rate may not imply a position size"
        assert result.reason, "a refusal must state why, not fail silently"

    def test_signature_has_no_invented_default(self) -> None:
        """The 0.5 default must not exist on the signature any more."""
        import inspect

        params = inspect.signature(RiskService.assess_trade).parameters
        assert (
            params["win_rate"].default is inspect.Parameter.empty
        ), "win_rate must be supplied explicitly; an unsupplied value is unknown, not 0.5"

    def test_explicit_out_of_range_win_rate_refuses_with_reason(self) -> None:
        svc = _live_risk()
        for bad in (0.0, 1.0, -0.1, 1.4):
            result = svc.assess_trade(
                price=100.0,
                confidence=0.8,
                atr=5.0,
                account_equity=10000.0,
                win_rate=bad,
            )
            assert result.kelly_fraction == 0.0
            assert result.reason

    def test_supplied_win_rate_still_sizes(self) -> None:
        """Supplying a real value must keep working: this is not a block."""
        svc = _live_risk()
        result = svc.assess_trade(
            price=100.0,
            confidence=0.8,
            atr=5.0,
            account_equity=10000.0,
            win_rate=0.62,
        )
        assert result.kelly_fraction > 0.0
        assert result.reason == ""


class TestLiveCycleRefusesUnsourcedSizing:
    def test_live_cycle_places_no_order_without_a_sourced_win_rate(self) -> None:
        metrics = InMemoryMetricsService()
        audit = InMemoryAuditService()
        broker = _CountingBroker()
        executor = _executor(_live_risk(), broker, metrics, audit)

        result = executor.run(uuid.uuid4(), 100.0)

        assert broker.submits == 0, "LIVE cycle placed an order from an unsourced win rate"
        assert result.trades == 0

    def test_refusal_is_recorded_not_swallowed(self) -> None:
        """No silent drop: the refusal is visible to operators."""
        metrics = InMemoryMetricsService()
        audit = InMemoryAuditService()
        broker = _CountingBroker()
        executor = _executor(_live_risk(), broker, metrics, audit)

        executor.run(uuid.uuid4(), 100.0)

        names = set(metrics.snapshot())
        assert any(
            "risk" in name for name in names
        ), f"an unsourced sizing refusal must be counted, not dropped silently; saw {sorted(names)}"

    def test_stop_levels_are_still_offered_on_refusal(self) -> None:
        """A refusal is about size, not about pretending ATR is unknown."""
        svc = _live_risk()
        result = svc.assess_trade(
            price=100.0,
            confidence=0.8,
            atr=5.0,
            account_equity=10000.0,
            win_rate=None,
        )
        assert result.suggested_stop_loss == 90.0
        assert result.suggested_take_profit == 115.0
        assert result.max_risk_amount == 200.0

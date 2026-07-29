from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from traderos.application.cycle_executor import CycleExecutor
from traderos.application.models import TradingMode
from traderos.domain.services.preflight_service import PreflightService
from traderos.domain.services.preflight_service import PreflightVerdict
from traderos.domain.services.risk_service import KillSwitch
from traderos.domain.services.risk_service import TradeVerdict
from traderos.domain.services.strategy_framework import SignalResult
from traderos.domain.services.strategy_framework import StrategyBase
from traderos.domain.services.strategy_framework import registry as strategy_registry


class _DummyStrategy(StrategyBase):
    name = "_preflight_test_strat"
    version = "1.0.0"

    def evaluate(self, state):
        return SignalResult(direction="long", confidence=0.8, metadata={"reason": "test"})


class _BrokerSpy:
    def __init__(self) -> None:
        self.place_market_order_called = False
        self.calls: list = []

    def place_market_order(self, market_id, side, quantity, close_price=None):
        self.place_market_order_called = True
        self.calls.append((market_id, side, quantity))
        from traderos.domain.adapters.broker_adapter import FillResult

        return FillResult(True, quantity, 100.0, 0.0, "filled", "ord-1")

    def place_limit_order(self, market_id, side, quantity, price, close_price=None):
        return None

    def cancel_order(self, order_id):
        return None

    def get_account_balance(self):
        return 10000.0

    def get_positions(self):
        return []

    def get_open_orders(self):
        return []


class _AlwaysFailPreflight(PreflightService):
    def check(self, live_mode: bool = False):
        return PreflightVerdict(
            passed=False,
            checks={"audit_chain": False},
            failures=["Preflight blocked: audit chain verification failed"],
        )


class _BlockedReconciliation:
    @property
    def can_accept_orders(self) -> bool:
        return False


class _BlockedKillSwitch(KillSwitch):
    def can_trade(self):
        return TradeVerdict(False, "Circuit breaker open")


def _make_executor(
    broker: _BrokerSpy,
    preflight: PreflightService | None = None,
) -> CycleExecutor:
    risk_service = MagicMock()
    risk_service.can_trade.return_value = TradeVerdict(True, "")
    risk_service.kill_switch = _BlockedKillSwitch()
    risk_service.assess_trade.return_value = MagicMock(kelly_fraction=0.5)

    portfolio_service = MagicMock()
    summary = MagicMock()
    summary.open_positions = []
    summary.total_equity = 10000.0
    portfolio_service.get_summary.return_value = summary
    portfolio_service.size_position.return_value = 0.5
    portfolio_service.open_trade.return_value = MagicMock(id=uuid.uuid4())

    analysis = MagicMock()
    analysis.compute_sma.return_value = []
    analysis.compute_atr.return_value = []

    signal_service = MagicMock()

    return CycleExecutor(
        mode=TradingMode.PAPER,
        signal_service=signal_service,
        risk_service=risk_service,
        portfolio_service=portfolio_service,
        execution=MagicMock(),
        analysis=analysis,
        broker=broker,
        event_bus=MagicMock(),
        health=MagicMock(),
        audit=MagicMock(),
        metrics=MagicMock(),
        notifications=MagicMock(),
        run_manifest=MagicMock(),
        data_ingestion=None,
        default_cash=10000.0,
        preflight_service=preflight,
    )


class _SignalProvenance:
    def __init__(self) -> None:
        self.signal = MagicMock()
        self.signal.direction.value = "long"
        self.signal.confidence = 0.8
        self.signal.id = uuid.uuid4()


class TestPreflightBlocksOrderSubmission:
    def _run_with_strategy(
        self,
        broker: _BrokerSpy,
        preflight: PreflightService | None = None,
    ) -> tuple[bool, list[str]]:
        executor = _make_executor(broker, preflight)
        executor._signal_service.process_evaluation.return_value = _SignalProvenance()
        strategy_registry.register(_DummyStrategy)
        result = executor.run(uuid.uuid4(), 50000.0)
        return broker.place_market_order_called, result.errors

    def test_preflight_failure_prevents_broker_call(self) -> None:
        called, errors = self._run_with_strategy(
            _BrokerSpy(),
            preflight=_AlwaysFailPreflight(),
        )
        assert not called, "Broker should NOT be called when preflight fails"
        assert any("preflight" in e for e in errors), f"Expected preflight errors, got: {errors}"

    def test_no_preflight_allows_broker_call(self) -> None:
        called, errors = self._run_with_strategy(_BrokerSpy(), preflight=None)
        assert called, "Broker SHOULD be called when no preflight blocks"

    def test_blocked_reconciliation_in_preflight_blocks_broker(self) -> None:
        blocked_recon = _BlockedReconciliation()
        preflight = PreflightService(broker_reconciliation=blocked_recon)
        called, errors = self._run_with_strategy(_BrokerSpy(), preflight=preflight)
        assert not called
        assert any("preflight" in e for e in errors), f"Expected preflight errors, got: {errors}"

    def test_engaged_kill_switch_in_preflight_blocks_broker(self) -> None:
        blocked_ks = _BlockedKillSwitch()
        preflight = PreflightService(kill_switch=blocked_ks)
        called, errors = self._run_with_strategy(_BrokerSpy(), preflight=preflight)
        assert not called
        assert any("preflight" in e for e in errors), f"Expected preflight errors, got: {errors}"

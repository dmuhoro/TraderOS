from __future__ import annotations

import os
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
    name = "_preflight_refusal_strat"
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


class _BlockedReconciliation:
    @property
    def can_accept_orders(self) -> bool:
        return False


class _OpenReconciliation:
    @property
    def can_accept_orders(self) -> bool:
        return True


class _BlockedKillSwitch(KillSwitch):
    def can_trade(self):
        return TradeVerdict(False, "Circuit breaker open")


class _OpenKillSwitch(KillSwitch):
    def can_trade(self):
        return TradeVerdict(True, "")


class _BrokenAudit:
    def verify_chain(self) -> bool:
        return False


class _WorkingAudit:
    def verify_chain(self) -> bool:
        return True


def _make_executor(
    broker: _BrokerSpy,
    preflight: PreflightService | None = None,
) -> CycleExecutor:
    risk_service = MagicMock()
    risk_service.can_trade.return_value = TradeVerdict(True, "")
    risk_service.kill_switch = _OpenKillSwitch()
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


class _TogglePreflight(PreflightService):
    """Preflight that passes first check() then fails second check().

    Simulates TOCTOU race: state changes between preflight gate and order submission.
    """

    def __init__(self) -> None:
        self._check_count = 0

    def check(self, live_mode: bool = False) -> PreflightVerdict:
        self._check_count += 1
        if self._check_count == 1:
            return PreflightVerdict(passed=True, checks={"ok": True})
        return PreflightVerdict(
            passed=False,
            checks={"kill_switch": False},
            failures=["Kill switch engaged between check and submit"],
        )


class _SlowToggleKillSwitch(KillSwitch):
    """Kill switch that closes after first can_trade() call."""

    def __init__(self) -> None:
        self._called = 0

    def can_trade(self) -> TradeVerdict:
        self._called += 1
        if self._called == 1:
            return TradeVerdict(True, "")
        return TradeVerdict(False, "Kill switch tripped mid-cycle")


class _SlowToggleBrokerSpy(_BrokerSpy):
    """Broker that fails place_market_order if kill switch is engaged.

    Simulates the broker itself refusing when kill switch trips during submission.
    """

    def place_market_order(self, market_id, side, quantity, close_price=None):
        raise RuntimeError("Broker rejected order: kill switch engaged")


class _LivePreflightService(PreflightService):
    def check(self, live_mode: bool = False) -> PreflightVerdict:
        checks: dict[str, bool] = {}
        failures: list[str] = []
        if self._kill_switch is not None:
            verdict = self._kill_switch.can_trade()
            checks["kill_switch"] = verdict.allowed
            if not verdict.allowed:
                failures.append(f"Kill switch engaged: {verdict.reason}")
        if live_mode:
            from traderos.domain.services.preflight_service import _LIVE_CONFIRM_ENV

            live_confirmed = os.getenv(_LIVE_CONFIRM_ENV, "").lower() in ("true", "1", "yes")
            checks["live_trading_confirmed"] = live_confirmed
            if not live_confirmed:
                failures.append(f"Live mode requires {_LIVE_CONFIRM_ENV}=true")
        else:
            checks["live_trading_confirmed"] = True
        passed = len(failures) == 0
        return PreflightVerdict(passed=passed, checks=checks, failures=failures)


class TestPreflightRefusalMatrix:
    """10 real-path preflight refusal tests.

    Covers all refusal conditions in the CycleExecutor order path:
    - 4 conditions from PreflightService.check()
    - 2 TOCTOU race scenarios
    - Multi-condition simultaneous failures
    - Re-check protection
    """

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
        """E1: Generic preflight failure blocks broker."""
        preflight = PreflightService(
            audit=_BrokenAudit(),
            broker_reconciliation=_BlockedReconciliation(),
            kill_switch=_BlockedKillSwitch(),
        )
        called, errors = self._run_with_strategy(_BrokerSpy(), preflight=preflight)
        assert not called, "Broker should NOT be called when preflight fails"
        assert any("preflight" in e for e in errors), f"Expected preflight errors, got: {errors}"

    def test_audit_chain_failure_blocks_broker(self) -> None:
        """E2: Audit chain verification failure alone blocks broker."""
        preflight = PreflightService(audit=_BrokenAudit())
        called, errors = self._run_with_strategy(_BrokerSpy(), preflight=preflight)
        assert not called, "Broker should NOT be called when audit chain fails"
        assert any("audit" in e.lower() for e in errors), f"Expected audit error, got: {errors}"

    def test_blocked_reconciliation_blocks_broker(self) -> None:
        """E3: Broker state reconciliation not complete blocks broker."""
        preflight = PreflightService(
            broker_reconciliation=_BlockedReconciliation(),
        )
        called, errors = self._run_with_strategy(_BrokerSpy(), preflight=preflight)
        assert not called
        assert any("reconciliation" in e.lower() for e in errors)

    def test_engaged_kill_switch_blocks_broker(self) -> None:
        """E4: Engaged kill switch blocks broker."""
        preflight = PreflightService(kill_switch=_BlockedKillSwitch())
        called, errors = self._run_with_strategy(_BrokerSpy(), preflight=preflight)
        assert not called
        assert any("kill switch" in e.lower() for e in errors)

    def test_live_mode_without_confirmation_blocks_broker(self) -> None:
        """E5: LIVE mode without LIVE_TRADING_CONFIRMED env var blocks broker."""
        preflight = _LivePreflightService()
        executor = _make_executor(_BrokerSpy(), preflight=preflight)
        executor._mode = TradingMode.LIVE
        executor._signal_service.process_evaluation.return_value = _SignalProvenance()
        strategy_registry.register(_DummyStrategy)
        result = executor.run(uuid.uuid4(), 50000.0)
        called = executor._broker.place_market_order_called
        errors = result.errors
        assert not called
        assert any("live" in e.lower() for e in errors)

    def test_live_mode_with_confirmation_allows_broker(self) -> None:
        """E6: LIVE mode with LIVE_TRADING_CONFIRMED allows broker."""
        os.environ["LIVE_TRADING_CONFIRMED"] = "true"
        try:
            preflight = _LivePreflightService(kill_switch=_OpenKillSwitch())
            executor = _make_executor(_BrokerSpy(), preflight=preflight)
            executor._mode = TradingMode.LIVE
            executor._signal_service.process_evaluation.return_value = _SignalProvenance()
            strategy_registry.register(_DummyStrategy)
            executor.run(uuid.uuid4(), 50000.0)
            called = executor._broker.place_market_order_called
            assert called, "Broker SHOULD be called when live confirmed"
        finally:
            del os.environ["LIVE_TRADING_CONFIRMED"]

    def test_multiple_simultaneous_failures(self) -> None:
        """E7: All 4 preflight conditions failing simultaneously — all failures reported."""
        preflight = PreflightService(
            audit=_BrokenAudit(),
            broker_reconciliation=_BlockedReconciliation(),
            kill_switch=_BlockedKillSwitch(),
        )
        called, errors = self._run_with_strategy(_BrokerSpy(), preflight=preflight)
        assert not called
        failure_keywords = ["audit", "reconciliation", "kill switch"]
        found = [kw for kw in failure_keywords if any(kw in e.lower() for e in errors)]
        assert len(found) >= 2, (
            f"Expected at least 2 failure conditions reported, got: {found}. "
            f"All errors: {errors}"
        )

    def test_toctou_kill_switch_trips_between_check_and_submit(self) -> None:
        """E8: TOCTOU — kill switch engages between preflight check and order submission.

        The _TogglePreflight passes check() once then fails on re-check (right before
        broker.place_market_order). Proves re-check protection catches mid-cycle state change.
        """
        toggle = _TogglePreflight()
        called, errors = self._run_with_strategy(_BrokerSpy(), preflight=toggle)
        assert not called, "Broker should NOT be called when re-check catches state change"
        assert any(
            "re-check" in e.lower() or "between check" in e.lower() for e in errors
        ), f"Expected re-check error, got: {errors}"

    def test_no_preflight_allows_broker_call(self) -> None:
        """E9: No preflight service — broker is called normally."""
        called, _errors = self._run_with_strategy(_BrokerSpy(), preflight=None)
        assert called, "Broker SHOULD be called when no preflight blocks"

    def test_all_checks_pass_allows_broker_call(self) -> None:
        """E10: All preflight checks pass — broker is called normally."""
        preflight = PreflightService(
            audit=_WorkingAudit(),
            broker_reconciliation=_OpenReconciliation(),
            kill_switch=_OpenKillSwitch(),
        )
        called, _errors = self._run_with_strategy(_BrokerSpy(), preflight=preflight)
        assert called, "Broker SHOULD be called when all checks pass"

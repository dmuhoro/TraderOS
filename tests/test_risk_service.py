from __future__ import annotations

import uuid
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta
from unittest.mock import Mock

from traderos.domain.entities import Position
from traderos.domain.services.risk_service import KillSwitch
from traderos.domain.services.risk_service import RiskService


class TestRiskService:
    def test_assess_trade(self) -> None:
        svc = RiskService()
        result = svc.assess_trade(
            price=100.0,
            confidence=0.7,
            atr=5.0,
            account_equity=10000.0,
        )
        assert 0 <= result.kelly_fraction <= 0.25
        assert result.suggested_stop_loss < 100.0
        assert result.suggested_take_profit > 100.0
        assert result.risk_per_unit > 0
        assert result.max_risk_amount == 200.0

    def test_assess_trade_zero_win_rate(self) -> None:
        svc = RiskService()
        result = svc.assess_trade(
            price=50.0,
            confidence=0.3,
            atr=2.0,
            account_equity=5000.0,
            win_rate=0.0,
        )
        assert result.kelly_fraction == 0.0

    def test_compute_var_empty(self) -> None:
        svc = RiskService()
        assert svc.compute_var([]) == 0.0

    def test_compute_var(self) -> None:
        svc = RiskService()
        mid = uuid.uuid4()
        positions = [
            Position(
                market_id=mid, quantity=10.0, entry_price=100.0, current_price=110.0, pnl=100.0
            ),
            Position(
                market_id=mid, quantity=5.0, entry_price=200.0, current_price=190.0, pnl=-50.0
            ),
        ]
        var = svc.compute_var(positions)
        assert var >= 0

    def test_compute_max_drawdown(self) -> None:
        svc = RiskService()
        curve = [100.0, 110.0, 90.0, 95.0, 80.0]
        dd = svc.compute_max_drawdown(curve)
        assert dd == 0.2727272727272727

    def test_check_concentration(self) -> None:
        svc = RiskService()
        mid = uuid.uuid4()
        positions = [
            Position(
                market_id=mid,
                quantity=100.0,
                entry_price=100.0,
                current_price=100.0,
                pnl=0.0,
            )
        ]
        risk = svc.check_concentration(positions)
        assert len(risk.concentration_risk) == 1
        assert risk.concentration_risk[0][1] == 1.0

    def test_enforce_limits(self) -> None:
        svc = RiskService()
        mid1 = uuid.uuid4()
        mid2 = uuid.uuid4()
        mid3 = uuid.uuid4()
        mid4 = uuid.uuid4()
        mid5 = uuid.uuid4()
        positions = [
            Position(
                market_id=mid1, quantity=2.0, entry_price=100.0, current_price=110.0, pnl=20.0
            ),
            Position(
                market_id=mid2, quantity=2.0, entry_price=100.0, current_price=110.0, pnl=20.0
            ),
            Position(
                market_id=mid3, quantity=2.0, entry_price=100.0, current_price=110.0, pnl=20.0
            ),
            Position(
                market_id=mid4, quantity=2.0, entry_price=100.0, current_price=110.0, pnl=20.0
            ),
            Position(
                market_id=mid5, quantity=2.0, entry_price=100.0, current_price=110.0, pnl=20.0
            ),
        ]
        risk = svc.check_concentration(positions)
        assert svc.enforce_limits(positions[0], risk)

    def test_kill_switch_allows_trade_by_default(self) -> None:
        svc = RiskService()
        verdict = svc.kill_switch.can_trade()
        assert verdict.allowed

    def test_kill_switch_opens_after_max_failures(self) -> None:
        ks = KillSwitch(max_consecutive_failures=3)
        for _ in range(3):
            ks.record_failure()
        verdict = ks.can_trade()
        assert not verdict.allowed
        assert "Circuit breaker open" in verdict.reason

    def test_kill_switch_manual_reset_only_no_auto_recovery(self) -> None:
        ks = KillSwitch(max_consecutive_failures=3)
        for _ in range(3):
            ks.record_failure()
        assert not ks.can_trade().allowed

        ks.reset()
        verdict = ks.can_trade()
        assert verdict.allowed

    def test_kill_switch_record_success_does_not_close_circuit(self) -> None:
        ks = KillSwitch(max_consecutive_failures=3)
        for _ in range(3):
            ks.record_failure()
        assert ks.circuit_open

        ks.record_success()
        assert ks.circuit_open
        assert not ks.can_trade().allowed

    def test_kill_switch_daily_loss_limit(self) -> None:
        ks = KillSwitch(daily_loss_limit=100.0)
        ks.daily_realized_pnl = -150.0
        verdict = ks.can_trade()
        assert not verdict.allowed
        assert "loss limit" in verdict.reason

    def test_kill_switch_threshold_not_reached_allows_trade(self) -> None:
        ks = KillSwitch(max_consecutive_failures=5)
        ks.record_failure()
        ks.record_failure()
        verdict = ks.can_trade()
        assert verdict.allowed

    def test_kill_switch_failure_count_resets_on_success(self) -> None:
        ks = KillSwitch(max_consecutive_failures=5)
        ks.record_failure()
        ks.record_failure()
        ks.record_success()
        verdict = ks.can_trade()
        assert verdict.allowed
        assert ks.consecutive_failures == 0

    def test_enforce_limits_violation(self) -> None:
        svc = RiskService()
        mid = uuid.uuid4()
        pos = Position(
            market_id=mid, quantity=10.0, entry_price=100.0, current_price=50.0, pnl=-500.0
        )
        risk = svc.check_concentration([pos])
        assert not svc.enforce_limits(pos, risk)

    def test_kill_switch_threshold_without_open_circuit_denies(self) -> None:
        ks = KillSwitch(max_consecutive_failures=5)
        ks.consecutive_failures = 5  # set directly: not circuit_open yet
        assert not ks.circuit_open
        verdict = ks.can_trade()
        assert not verdict.allowed
        assert "consecutive failures" in verdict.reason

    def test_kill_switch_realized_pnl_rolls_over_day(self) -> None:
        ks = KillSwitch()
        ks._current_day = date(2000, 1, 1)
        ks.daily_realized_pnl = -50.0
        ks.record_realized_pnl(-10.0)
        assert ks._current_day == date(2026, 8, 11) or ks._current_day != date(2000, 1, 1)
        assert ks.daily_realized_pnl == -10.0

    def test_authorize_order_non_positive_equity(self) -> None:
        svc = RiskService()
        verdict = svc.authorize_order(uuid.uuid4(), "buy", 1.0, 100.0, equity=0.0)
        assert not verdict.allowed
        assert "non-positive equity" in verdict.reason

    def test_authorize_order_stale_market_data(self) -> None:
        svc = RiskService(max_data_staleness_seconds=60.0)
        now = datetime.now(UTC)
        verdict = svc.authorize_order(
            uuid.uuid4(),
            "buy",
            1.0,
            100.0,
            equity=10000.0,
            last_candle_at=now - timedelta(seconds=120),
            now=now,
        )
        assert not verdict.allowed
        assert "stale" in verdict.reason

    def test_authorize_order_existing_exposure_over_cap(self) -> None:
        svc = RiskService(max_gross_exposure=0.5)
        verdict = svc.authorize_order(
            uuid.uuid4(), "buy", 1.0, 100.0, equity=10000.0, existing_gross_exposure=6000.0
        )
        assert not verdict.allowed
        assert "already exceeds cap" in verdict.reason

    def test_authorize_order_respects_kill_switch(self) -> None:
        svc = RiskService(kill_switch=KillSwitch())
        svc.kill_switch.engage()
        verdict = svc.authorize_order(uuid.uuid4(), "buy", 1.0, 100.0, equity=10000.0)
        assert not verdict.allowed
        assert "Circuit breaker open" in verdict.reason

    def test_kill_switch_disengage(self) -> None:
        ks = KillSwitch()
        ks.engage()
        ks.record_failure()
        ks.disengage()
        assert not ks.circuit_open
        assert ks.consecutive_failures == 0
        assert ks.can_trade().allowed

    def test_can_trade_kill_switch_denied_audits(self) -> None:
        audit = Mock()
        metrics = Mock()
        svc = RiskService(kill_switch=KillSwitch(), audit=audit, metrics=metrics)
        svc.kill_switch.engage()
        verdict = svc.can_trade([])
        assert not verdict.allowed
        assert "Circuit breaker open" in verdict.reason
        audit.record.assert_called_once()
        metrics.counter.assert_called_once_with("circuit_breaker.tripped", 1.0)

    def test_authorize_order_explicit_daily_loss_limit(self) -> None:
        svc = RiskService(kill_switch=KillSwitch(daily_loss_limit=100.0))
        svc.kill_switch.daily_realized_pnl = -150.0
        verdict = svc.authorize_order(uuid.uuid4(), "buy", 1.0, 100.0, equity=10000.0)
        assert not verdict.allowed
        assert "Daily loss limit reached" in verdict.reason

    def test_persistent_kill_switch_denies_and_audits(self) -> None:
        persistent = Mock()
        persistent.can_trade.return_value = False
        audit = Mock()
        metrics = Mock()
        svc = RiskService(persistent_kill_switch=persistent, audit=audit, metrics=metrics)
        verdict = svc.can_trade([], user_id=None)
        assert not verdict.allowed
        assert "Persistent kill switch" in verdict.reason
        audit.record.assert_called_once()
        metrics.counter.assert_called_once_with("circuit_breaker.tripped", 1.0)

    def test_compute_var_zero_value_returns_zero(self) -> None:
        svc = RiskService()
        pos = Position(
            market_id=uuid.uuid4(), quantity=0.0, entry_price=100.0, current_price=110.0, pnl=0.0
        )
        assert svc.compute_var([pos]) == 0.0

    def test_compute_max_drawdown_empty(self) -> None:
        assert RiskService().compute_max_drawdown([]) == 0.0

    def test_check_concentration_empty(self) -> None:
        risk = RiskService().check_concentration([])
        assert risk.var_95 == 0.0
        assert risk.num_over_limit == 0
        assert risk.concentration_risk == []

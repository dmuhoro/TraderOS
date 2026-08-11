from __future__ import annotations

from traderos.domain.services.preflight_service import PreflightService
from traderos.domain.services.risk_service import KillSwitch


class _ValidAudit:
    def verify_chain(self) -> bool:
        return True

    def record(self, action, actor, resource, detail=""):
        pass

    def get_entries(self, limit=100, offset=0):
        return []

    def find(self, action=None, actor=None):
        return []


class _BrokenAudit:
    def verify_chain(self) -> bool:
        return False

    def record(self, action, actor, resource, detail=""):
        pass

    def get_entries(self, limit=100, offset=0):
        return []

    def find(self, action=None, actor=None):
        return []


class _ReconciledBrokerRecon:
    @property
    def can_accept_orders(self) -> bool:
        return True


class _UnreconciledBrokerRecon:
    @property
    def can_accept_orders(self) -> bool:
        return False


class TestPreflightService:
    def test_all_checks_pass_with_no_dependencies(self) -> None:
        svc = PreflightService()
        verdict = svc.check()
        assert verdict.passed
        assert verdict.checks["audit_chain"]
        assert verdict.checks["broker_reconciliation"]
        assert verdict.checks["kill_switch"]
        assert verdict.checks["live_trading_confirmed"]

    def test_audit_chain_failure_captured(self) -> None:
        svc = PreflightService(audit=_BrokenAudit())
        verdict = svc.check()
        assert not verdict.passed
        assert "Audit chain verification failed" in verdict.failures

    def test_audit_chain_passes(self) -> None:
        svc = PreflightService(audit=_ValidAudit())
        verdict = svc.check()
        assert verdict.passed

    def test_unreconciled_broker_fails(self) -> None:
        svc = PreflightService(broker_reconciliation=_UnreconciledBrokerRecon())
        verdict = svc.check()
        assert not verdict.passed
        assert any("Broker state reconciliation incomplete" in f for f in verdict.failures)

    def test_kill_switch_engaged_fails(self) -> None:
        ks = KillSwitch(max_consecutive_failures=3)
        for _ in range(3):
            ks.record_failure()
        svc = PreflightService(kill_switch=ks)
        verdict = svc.check()
        assert not verdict.passed
        assert any("Kill switch engaged" in f for f in verdict.failures)

    def test_live_mode_requires_explicit_confirmation(self, monkeypatch) -> None:
        monkeypatch.delenv("LIVE_TRADING_CONFIRMED", raising=False)
        svc = PreflightService()
        verdict = svc.check(live_mode=True)
        assert not verdict.passed
        assert any("LIVE_TRADING_CONFIRMED" in f for f in verdict.failures)

    def test_live_mode_passes_with_confirmation(self, monkeypatch) -> None:
        monkeypatch.setenv("LIVE_TRADING_CONFIRMED", "true")
        svc = PreflightService()
        verdict = svc.check(live_mode=True)
        assert verdict.passed

    def test_live_mode_fails_with_wrong_confirmation(self, monkeypatch) -> None:
        monkeypatch.setenv("LIVE_TRADING_CONFIRMED", "false")
        svc = PreflightService()
        verdict = svc.check(live_mode=True)
        assert not verdict.passed

    def test_live_mode_requires_allowlist_when_configured(self, monkeypatch) -> None:
        monkeypatch.setenv("LIVE_TRADING_CONFIRMED", "true")
        svc = PreflightService(require_allowlist=True)
        verdict = svc.check(live_mode=True)
        assert not verdict.passed
        assert verdict.checks["market_allowlist"] is False
        assert any("allowlist" in f for f in verdict.failures)

    def test_live_mode_allowlist_provided_passes(self, monkeypatch) -> None:
        monkeypatch.setenv("LIVE_TRADING_CONFIRMED", "true")
        svc = PreflightService(require_allowlist=True, allowed_markets=frozenset({"X"}))
        verdict = svc.check(live_mode=True)
        assert verdict.passed
        assert verdict.checks["market_allowlist"] is True

    def test_multiple_failures_all_reported(self) -> None:
        ks = KillSwitch(max_consecutive_failures=3)
        for _ in range(3):
            ks.record_failure()
        svc = PreflightService(
            audit=_BrokenAudit(),
            broker_reconciliation=_UnreconciledBrokerRecon(),
            kill_switch=ks,
        )
        verdict = svc.check(live_mode=True)
        assert not verdict.passed
        assert len(verdict.failures) >= 3  # audit + broker + kill_switch (live may also fail)

    def test_verdict_is_truthy_on_pass(self) -> None:
        svc = PreflightService()
        verdict = svc.check()
        assert bool(verdict)

    def test_verdict_is_falsy_on_fail(self) -> None:
        svc = PreflightService(audit=_BrokenAudit())
        verdict = svc.check()
        assert not bool(verdict)

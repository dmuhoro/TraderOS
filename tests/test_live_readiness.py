from __future__ import annotations

import uuid
from unittest.mock import Mock

from traderos.domain.collectors.base import CollectorType
from traderos.domain.services.data_ingestion_service import DataIngestionService
from traderos.domain.services.live_readiness import LiveReadinessService
from traderos.domain.services.operator_session import OperatorSessionService
from traderos.domain.services.risk_service import KillSwitch


class _BrokerStub:
    balance = 10000.0
    reachable = True

    def get_account_balance(self):
        if not self.reachable:
            raise RuntimeError("connection refused")
        return self.balance


def _ingestion(sources: int = 2) -> DataIngestionService:
    ingestion = DataIngestionService(registry=Mock())
    for i in range(sources):
        ingestion.add_source(uuid.uuid4(), f"SYM{i}", CollectorType.MOCK)
    return ingestion


def _preflight(ok: bool = True):
    verdict = Mock(passed=ok, failures=[] if ok else ["boom"])
    service = Mock()
    service.check.return_value = verdict
    return service


def _session() -> OperatorSessionService:
    session = Mock()
    session.workflow.session_id = "session-1"
    return session


def _service(**kwargs) -> LiveReadinessService:
    defaults = {
        "broker": _BrokerStub(),
        "data_ingestion": _ingestion(),
        "preflight": _preflight(),
        "kill_switch": KillSwitch(),
        "operator_session": _session(),
    }
    defaults.update(kwargs)
    return LiveReadinessService(**defaults)


class TestLiveReadiness:
    def test_ready_when_all_preconditions_pass(self) -> None:
        verdict = _service().check()
        assert verdict.ready
        assert verdict.dry_run is True
        assert verdict.live_execution_enabled is False
        assert all(verdict.checks.values())

    def test_reports_live_execution_enabled_flag(self) -> None:
        verdict = _service(live_execution_enabled=True).check()
        assert verdict.live_execution_enabled is True
        assert verdict.ready

    def test_requires_operator_session(self) -> None:
        session = Mock()
        session.workflow.session_id = None
        verdict = _service(operator_session=session).check()
        assert not verdict.ready
        assert verdict.checks["operator_session"] is False
        assert any("operator session" in r for r in verdict.reasons)

    def test_fails_when_kill_switch_engaged(self) -> None:
        ks = KillSwitch()
        ks.engage()
        verdict = _service(kill_switch=ks).check()
        assert not verdict.ready
        assert verdict.checks["kill_switch_closed"] is False

    def test_fails_without_data_sources(self) -> None:
        verdict = _service(data_ingestion=_ingestion(sources=0)).check()
        assert not verdict.ready
        assert verdict.checks["data_feeds"] is False

    def test_fails_when_broker_unreachable(self) -> None:
        broker = _BrokerStub()
        broker.reachable = False
        verdict = _service(broker=broker).check()
        assert not verdict.ready
        assert verdict.checks["broker_connected"] is False
        assert any("broker unreachable" in r for r in verdict.reasons)

    def test_fails_when_live_preflight_fails(self) -> None:
        verdict = _service(preflight=_preflight(ok=False)).check()
        assert not verdict.ready
        assert verdict.checks["live_preflight"] is False
        assert "boom" in verdict.reasons

    def test_to_dict_shape(self) -> None:
        data = _service().check().to_dict()
        assert data["ready"] is True
        assert data["dry_run"] is True
        assert data["live_execution_enabled"] is False
        assert isinstance(data["checks"], dict)
        assert isinstance(data["reasons"], list)
        assert "timestamp" in data

    def test_broker_not_configured_fails_closed(self) -> None:
        verdict = _service(broker=None).check()
        assert not verdict.ready
        assert verdict.checks["broker_connected"] is False
        assert any("broker not configured" in r for r in verdict.reasons)

    def test_broker_non_positive_balance_fails(self) -> None:
        broker = _BrokerStub()
        broker.balance = 0.0
        verdict = _service(broker=broker).check()
        assert not verdict.ready
        assert verdict.checks["broker_connected"] is False
        assert any("balance unavailable or non-positive" in r for r in verdict.reasons)

    def test_data_ingestion_not_configured_fails(self) -> None:
        verdict = _service(data_ingestion=None).check()
        assert not verdict.ready
        assert verdict.checks["data_feeds"] is False
        assert any("market data not configured" in r for r in verdict.reasons)

    def test_kill_switch_not_configured_defaults_closed(self) -> None:
        verdict = _service(kill_switch=None).check()
        assert verdict.checks["kill_switch_closed"] is True

    def test_preflight_not_configured_defaults_ok(self) -> None:
        verdict = _service(preflight=None).check()
        assert verdict.checks["live_preflight"] is True

    def test_operator_session_not_configured_defaults_ok(self) -> None:
        verdict = _service(operator_session=None).check()
        assert verdict.checks["operator_session"] is True

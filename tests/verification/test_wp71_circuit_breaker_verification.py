"""Release-gate tests for WP-7.1; these encode the ADR's required contract."""

from traderos.domain.services.risk_service import KillSwitch, RiskService
from traderos.infrastructure.metrics import MetricsService


def test_manual_reset_is_the_only_documented_recovery_path() -> None:
    kill_switch = KillSwitch(max_consecutive_failures=5)
    for _ in range(5):
        kill_switch.record_failure()

    assert not kill_switch.can_trade().allowed
    kill_switch.reset()
    assert kill_switch.can_trade().allowed


def test_circuit_breaker_trip_is_exposed_by_metrics_port() -> None:
    metrics = MetricsService()
    service = RiskService(metrics=metrics)
    for _ in range(5):
        service.kill_switch.record_failure()
    service.can_trade([])

    assert metrics.snapshot()["circuit_breaker.tripped"] == 1.0

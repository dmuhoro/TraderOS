from __future__ import annotations

import pytest

from traderos.domain.services.notification_service import NotificationLevel
from traderos.domain.services.notification_service import NotificationService
from traderos.infrastructure.notifiers.oncall_router import OnCallDeliveryError
from traderos.infrastructure.notifiers.oncall_router import OnCallRouter


class _NullAudit:
    def __init__(self) -> None:
        self.entries: list[str] = []

    def record(self, action: str, actor: str, resource: str, detail: str = "") -> None:
        self.entries.append(action)


class _NullMetrics:
    def __init__(self) -> None:
        self.counters: dict[str, float] = {}

    def counter(self, name: str, delta: float = 1.0) -> float:
        self.counters[name] = self.counters.get(name, 0.0) + delta
        return self.counters[name]

    def get_counter(self, name: str) -> float:
        return self.counters.get(name, 0.0)


class _FakeTransport:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.delivered: list[tuple] = []

    def deliver(
        self,
        title: str,
        message: str,
        level: NotificationLevel,
        metadata: dict[str, str | float | int | None],
    ) -> None:
        if self.fail:
            raise OnCallDeliveryError("endpoint refused")
        self.delivered.append((title, message, level, metadata))


class TestOnCallRouter:
    def test_below_threshold_stays_local(self) -> None:
        t = _FakeTransport()
        router = OnCallRouter([t], min_severity=NotificationLevel.CRITICAL, audit=_NullAudit())
        ok = router.route(NotificationLevel.WARNING, "t", "m")
        assert ok is True
        assert t.delivered == []

    def test_critical_delivered_on_ack(self) -> None:
        t = _FakeTransport()
        audit = _NullAudit()
        metrics = _NullMetrics()
        router = OnCallRouter([t], audit=audit, metrics=metrics)
        ok = router.route(NotificationLevel.CRITICAL, "kill trip", "flatten forced")
        assert ok is True
        assert len(t.delivered) == 1
        assert audit.entries.count("oncall.delivered") == 1
        assert metrics.get_counter("oncall.delivered") == 1.0

    def test_critical_failure_raises_and_is_audited(self) -> None:
        t = _FakeTransport(fail=True)
        audit = _NullAudit()
        metrics = _NullMetrics()
        router = OnCallRouter([t], audit=audit, metrics=metrics)
        with pytest.raises(OnCallDeliveryError):
            router.route(NotificationLevel.CRITICAL, "unclean death", "killed")
        assert audit.entries.count("oncall.delivery_failed") == 1
        assert metrics.get_counter("oncall.delivery_failed") == 1.0

    def test_error_failure_returns_false_not_raise(self) -> None:
        t = _FakeTransport(fail=True)
        audit = _NullAudit()
        router = OnCallRouter([t], min_severity=NotificationLevel.ERROR, audit=audit)
        ok = router.route(NotificationLevel.ERROR, "partial", "breach")
        assert ok is False
        assert audit.entries.count("oncall.delivery_failed") == 1

    def test_fanout_any_transport_succeeds(self) -> None:
        t1 = _FakeTransport(fail=True)
        t2 = _FakeTransport()
        audit = _NullAudit()
        router = OnCallRouter([t1, t2], audit=audit)
        ok = router.route(NotificationLevel.CRITICAL, "kill trip", "flatten forced")
        assert ok is True
        assert audit.entries.count("oncall.delivered") == 1


class TestNotificationServiceOncallIntegration:
    def test_critical_routes_through_oncall(self) -> None:
        t = _FakeTransport()
        router = OnCallRouter([t], audit=_NullAudit())
        notifications = NotificationService(notifier=None, oncall=router)
        event = notifications.critical("kill trip", "flatten forced")
        assert event.level == NotificationLevel.CRITICAL
        assert len(t.delivered) == 1
        assert t.delivered[0][0] == "kill trip"

    def test_info_does_not_route_externally(self) -> None:
        t = _FakeTransport()
        router = OnCallRouter([t])
        notifications = NotificationService(notifier=None, oncall=router)
        notifications.info("ping", "heartbeat")
        assert t.delivered == []

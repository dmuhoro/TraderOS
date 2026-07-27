from __future__ import annotations

from traderos.infrastructure.health import HealthService


class TestHealthService:
    def test_register_and_check(self) -> None:
        svc = HealthService()
        svc.register("db")
        assert svc.get_status("db") is True

    def test_report_healthy(self) -> None:
        svc = HealthService()
        status = svc.report_healthy("api", "all endpoints responding")
        assert status.healthy
        assert status.message == "all endpoints responding"

    def test_report_unhealthy(self) -> None:
        svc = HealthService()
        status = svc.report_unhealthy("db", "connection timeout")
        assert not status.healthy
        assert status.message == "connection timeout"

    def test_all_healthy_true(self) -> None:
        svc = HealthService()
        svc.register("a")
        svc.register("b")
        assert svc.all_healthy()

    def test_all_healthy_false(self) -> None:
        svc = HealthService()
        svc.register("a", initial=True)
        svc.register("b", initial=False)
        assert not svc.all_healthy()

    def test_check_with_pass(self) -> None:
        svc = HealthService()
        svc.check("test", lambda: True)
        assert svc.get_status("test") is True

    def test_check_with_fail(self) -> None:
        svc = HealthService()
        svc.check("test", lambda: False)
        assert svc.get_status("test") is False

    def test_check_with_exception(self) -> None:
        svc = HealthService()
        svc.check("test", lambda: (_ for _ in ()).throw(RuntimeError("oops")))
        assert svc.get_status("test") is False

    def test_summary(self) -> None:
        svc = HealthService()
        svc.register("a")
        svc.register("b", initial=False)
        summary = svc.summary()
        assert summary == {"a": True, "b": False}

    def test_history_limit(self) -> None:
        svc = HealthService()
        for i in range(5):
            svc.report_healthy(f"s{i}")
        assert len(svc.history(limit=3)) == 3

    def test_empty_all_healthy(self) -> None:
        svc = HealthService()
        assert svc.all_healthy()

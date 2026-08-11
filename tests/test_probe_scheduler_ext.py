from __future__ import annotations

import itertools
import logging
import threading
import time
import uuid
from types import SimpleNamespace

import traderos.infrastructure.probe_scheduler as probe_mod
from traderos.infrastructure.notifiers.oncall_router import OnCallDeliveryError
from traderos.infrastructure.probe_scheduler import ProbeResult
from traderos.infrastructure.probe_scheduler import ProbeScheduler
from traderos.infrastructure.probe_scheduler import broker_health_probe
from traderos.infrastructure.probe_scheduler import health_probe
from traderos.infrastructure.probe_scheduler import rate_limit_probe
from traderos.infrastructure.probe_scheduler import vault_probe


class _MemoryAudit:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str, str, str]] = []

    def record(self, action: str, actor: str, resource: str, detail: str = ""):
        self.entries.append((action, actor, resource, detail))


class _MemoryMetrics:
    def __init__(self) -> None:
        self.counters: dict[str, float] = {}
        self.gauges: dict[str, float] = {}

    def counter(self, name: str, delta: float = 1.0) -> float:
        self.counters[name] = self.counters.get(name, 0.0) + delta
        return self.counters[name]

    def gauge(self, name: str, value: float) -> None:
        self.gauges[name] = value


def _fast_clock(step: float):
    counter = itertools.count(0, step)
    return lambda: next(counter)


class TestHealthProbeSuccessPaths:
    def test_health_probe_success(self, monkeypatch) -> None:
        class _Resp:
            status_code = 200

        monkeypatch.setattr("requests.get", lambda *a, **k: _Resp())
        result = health_probe("http://x")
        assert result.ok is True
        assert "status=200" in result.detail

    def test_health_probe_bad_status(self, monkeypatch) -> None:
        class _Resp:
            status_code = 503

        monkeypatch.setattr("requests.get", lambda *a, **k: _Resp())
        result = health_probe("http://x")
        assert result.ok is False
        assert "status=503" in result.detail

    def test_health_probe_over_budget(self, monkeypatch) -> None:
        class _Resp:
            status_code = 200

        monkeypatch.setattr("requests.get", lambda *a, **k: _Resp())
        monkeypatch.setattr(probe_mod, "time", SimpleNamespace(perf_counter=_fast_clock(0.01)))
        result = health_probe("http://x", budget_ms=1.0)
        assert result.ok is False
        assert "over 1ms budget" in result.detail


class TestVaultProbeSuccessPaths:
    def test_vault_probe_success(self, monkeypatch) -> None:
        monkeypatch.setattr(probe_mod, "time", SimpleNamespace(perf_counter=_fast_clock(0.001)))
        result = vault_probe(lambda key: "secret-value", "KEY")
        assert result.ok is True
        assert "read in" in result.detail

    def test_vault_probe_missing_key(self) -> None:
        result = vault_probe(lambda key: None, "KEY")
        assert result.ok is False
        assert "no value" in result.detail

    def test_vault_probe_over_budget(self, monkeypatch) -> None:
        monkeypatch.setattr(probe_mod, "time", SimpleNamespace(perf_counter=_fast_clock(0.01)))
        result = vault_probe(lambda key: "v", "KEY", budget_ms=1.0)
        assert result.ok is False
        assert "over 1ms budget" in result.detail


class TestRateLimitProbeEdge:
    def test_rate_limit_probe_limiter_raises(self) -> None:
        class _Raising:
            def check(self, key):
                raise RuntimeError("limiter exploded")

        result = rate_limit_probe(_Raising())
        assert result.ok is False
        assert "limiter exploded" in result.detail


class TestBrokerHealthProbeEdges:
    def test_broker_probe_live_slow(self, monkeypatch) -> None:
        class _LiveBroker:
            def get_account_balance(self) -> float:
                return 10000.0

            def get_open_orders(self) -> list:
                return []

        monkeypatch.setattr(probe_mod, "time", SimpleNamespace(perf_counter=_fast_clock(1.5)))
        result = broker_health_probe(_LiveBroker(), "live", [uuid.uuid4()])
        assert result.ok is False
        assert "SLOW" in result.detail

    def test_broker_probe_rejected_order(self) -> None:
        class _Rejecting:
            def place_limit_order(self, market_id, side, quantity, price, close_price=None):
                return SimpleNamespace(filled=False, status="rejected", order_id=None)

        result = broker_health_probe(_Rejecting(), "paper", [uuid.uuid4()])
        assert result.ok is False
        assert "rejected" in result.detail

    def test_broker_probe_paper_slow(self, monkeypatch) -> None:
        class _Healthy:
            def place_limit_order(self, market_id, side, quantity, price, close_price=None):
                return SimpleNamespace(filled=False, status="pending", order_id="o1")

            def cancel_order(self, order_id):
                return None

        monkeypatch.setattr(probe_mod, "time", SimpleNamespace(perf_counter=_fast_clock(0.5)))
        result = broker_health_probe(_Healthy(), "paper", [uuid.uuid4()])
        assert result.ok is False
        assert "SLOW" in result.detail


class TestProbeSchedulerEdges:
    def test_start_is_idempotent(self) -> None:
        sched = ProbeScheduler([])
        sched.start()
        thread = sched._thread
        sched.start()
        assert sched._thread is thread
        sched.stop()

    def test_run_once_wraps_raising_probe(self) -> None:
        def exploding_probe():
            raise RuntimeError("boom")

        sched = ProbeScheduler([exploding_probe], audit=_MemoryAudit(), metrics=_MemoryMetrics())
        snapshot = sched.run_once()
        assert snapshot["exploding_probe"].ok is False
        assert "boom" in snapshot["exploding_probe"].detail

    def test_page_delivery_error_is_logged_not_raised(self, caplog) -> None:
        class _FailingRouter:
            def route(self, level, title, message, meta):
                raise OnCallDeliveryError("transport down")

        sched = ProbeScheduler(
            [lambda: ProbeResult(name="broker", ok=False, latency_ms=1.0, detail="down")],
            oncall=_FailingRouter(),
            audit=_MemoryAudit(),
            metrics=_MemoryMetrics(),
        )
        with caplog.at_level(logging.ERROR):
            sched.run_once()
        assert "probe page could not be delivered" in caplog.text

    def test_loop_survives_run_once_error(self, caplog) -> None:
        class _ExplodingMetrics:
            def counter(self, name, delta=1.0):
                return 0.0

            def gauge(self, name, value):
                raise RuntimeError("metrics down")

        sched = ProbeScheduler(
            [lambda: ProbeResult(name="broker", ok=False, latency_ms=1.0, detail="down")],
            metrics=_ExplodingMetrics(),
            interval_seconds=0.01,
        )

        def _stop_later():
            time.sleep(0.06)
            sched._stop_event.set()

        threading.Thread(target=_stop_later, daemon=True).start()
        with caplog.at_level(logging.ERROR):
            sched._loop()
        assert "probe scheduler tick failed" in caplog.text

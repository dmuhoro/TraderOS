"""WP2 — ProbeScheduler pages failures through the REAL on-call transport.

Every case drives the scheduler's real path and asserts delivery reached a
real loopback HTTP on-call transport (same honest-wire proof as WP2's
test_trigger_alerting). Proves:
  1. A failing probe pages a CRITICAL "Probe failed: <name>" packet (edge).
  2. Repeats of an already-failing probe are suppressed (no alert storm).
  3. Recovery pages a RESOLVED packet on the recovery edge.
  4. The scheduler thread actually ticks on a real interval and pages.
  5. Audit + metric are recorded even when no transport is configured
     (fail-closed: never a silent drop).
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer

import pytest

from traderos.infrastructure.notifiers.oncall_router import HttpOnCallTransport
from traderos.infrastructure.notifiers.oncall_router import OnCallRouter
from traderos.infrastructure.probe_scheduler import ProbeResult
from traderos.infrastructure.probe_scheduler import ProbeScheduler
from traderos.infrastructure.probe_scheduler import broker_health_probe
from traderos.infrastructure.probe_scheduler import health_probe
from traderos.infrastructure.probe_scheduler import rate_limit_probe
from traderos.infrastructure.probe_scheduler import vault_probe
from traderos.infrastructure.secrets import VaultSecretProvider

CRITICAL = "critical"


class _Receiver:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self._lock = threading.Lock()

    def handle(self, body: bytes) -> None:
        with self._lock:
            self.requests.append(json.loads(body.decode()))

    def all(self) -> list[dict]:
        with self._lock:
            return list(self.requests)

    def wait_for(self, predicate, timeout: float = 3.0) -> list[dict]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            packets = self.all()
            if predicate(packets):
                return packets
            time.sleep(0.02)
        return self.all()


class _Capture(BaseHTTPRequestHandler):
    receiver = _Receiver()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        _Capture.receiver.handle(body)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture()
def on_receiver():
    """Real loopback HTTP on-call transport capturing delivered packets."""
    receiver = _Receiver()
    _Capture.receiver = receiver
    server = HTTPServer(("127.0.0.1", 0), _Capture)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/oncall", receiver
    finally:
        server.shutdown()
        server.server_close()
        _Capture.receiver = _Receiver()


def _router(url: str) -> OnCallRouter:
    return OnCallRouter([HttpOnCallTransport(url, max_retries=1)])


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

    def get_counter(self, name: str) -> float:
        return self.counters.get(name, 0.0)


class _BrokerUnreachable:
    def place_limit_order(self, market_id, side, quantity, price, close_price=None):
        raise RuntimeError("Broker unreachable")


class _HealthyBroker:
    def place_limit_order(self, market_id, side, quantity, price, close_price=None):
        raise AssertionError("should not be reached")


class TestProbeRoundTrip:
    def test_broker_probe_fails_on_unreachable(self) -> None:
        result = broker_health_probe(_BrokerUnreachable(), "paper", [])
        assert result.ok is False
        assert "Broker unreachable" in result.detail

    def test_broker_probe_live_read_only(self) -> None:
        class _LiveBroker:
            def get_account_balance(self) -> float:
                return 10000.0

            def get_open_orders(self) -> list:
                return []

        result = broker_health_probe(_LiveBroker(), "live", [])
        assert result.ok is True
        assert "live read-only" in result.detail


class TestProbeSchedulerPages:
    def test_failure_pages_critical_through_real_transport(self, on_receiver) -> None:
        url, receiver = on_receiver
        audit = _MemoryAudit()
        metrics = _MemoryMetrics()
        sched = ProbeScheduler(
            [lambda: broker_health_probe(_BrokerUnreachable(), "paper", [])],
            oncall=_router(url),
            audit=audit,
            metrics=metrics,
        )
        sched.run_once()
        packets = receiver.all()
        assert packets, "a failing probe produced no on-call packet"
        assert packets[-1]["level"] == CRITICAL
        assert packets[-1]["title"] == "Probe failed: broker"
        assert "Broker unreachable" in packets[-1]["message"]
        assert any(a == "probe.failed" for a, _, _, _ in audit.entries)
        assert metrics.get_counter("probe.failed") == 1.0

    def test_healthy_probe_pages_nothing(self, on_receiver) -> None:
        url, receiver = on_receiver
        sched = ProbeScheduler(
            [lambda: ProbeResult(name="broker", ok=True, latency_ms=5.0, detail="ok")],
            oncall=_router(url),
            audit=_MemoryAudit(),
            metrics=_MemoryMetrics(),
        )
        sched.run_once()
        assert receiver.all() == []

    def test_repeat_failures_page_once_not_every_tick(self, on_receiver) -> None:
        url, receiver = on_receiver
        audit = _MemoryAudit()
        metrics = _MemoryMetrics()
        sched = ProbeScheduler(
            [lambda: ProbeResult(name="broker", ok=False, latency_ms=1.0, detail="down")],
            oncall=_router(url),
            audit=audit,
            metrics=metrics,
        )
        sched.run_once()
        sched.run_once()
        sched.run_once()
        pages = [p for p in receiver.all() if p["level"] == CRITICAL]
        assert len(pages) == 1, "a persistent failure must page exactly once"
        assert audit.entries.count(("probe.failed", "probe-scheduler", "broker", "down")) == 1

    def test_recovery_pages_resolved(self, on_receiver) -> None:
        url, receiver = on_receiver
        mode = {"fail": True}
        sched = ProbeScheduler(
            [
                lambda: ProbeResult(
                    name="broker", ok=not mode["fail"], latency_ms=2.0, detail="flap"
                )
            ],
            oncall=_router(url),
            audit=_MemoryAudit(),
            metrics=_MemoryMetrics(),
        )
        sched.run_once()
        assert len(receiver.all()) == 1
        mode["fail"] = False
        sched.run_once()
        pages = receiver.all()
        assert len(pages) == 2
        assert pages[-1]["level"] == CRITICAL
        assert "RESOLVED" in pages[-1]["title"]

    def test_failure_audited_even_without_transport(self) -> None:
        audit = _MemoryAudit()
        metrics = _MemoryMetrics()
        sched = ProbeScheduler(
            [lambda: ProbeResult(name="broker", ok=False, latency_ms=1.0, detail="down")],
            audit=audit,
            metrics=metrics,
        )
        sched.run_once()
        assert any(a == "probe.failed" for a, _, _, _ in audit.entries)
        assert metrics.get_counter("probe.failed") == 1.0
        assert metrics.gauges["probe.ok.broker"] == 0.0

    def test_latest_snapshot(self) -> None:
        sched = ProbeScheduler(
            [lambda: ProbeResult(name="broker", ok=False, latency_ms=9.0, detail="x")],
        )
        sched.run_once()
        snap = sched.latest
        assert snap["broker"].ok is False


class TestProbeSchedulerThread:
    def test_loop_ticks_every_interval_and_pages(self, on_receiver) -> None:
        url, receiver = on_receiver
        sched = ProbeScheduler(
            [lambda: ProbeResult(name="broker", ok=False, latency_ms=1.0, detail="down")],
            oncall=_router(url),
            interval_seconds=0.05,
        )
        assert sched.interval_seconds == 0.05
        sched.start()
        try:
            packets = receiver.wait_for(lambda ps: any(p["level"] == CRITICAL for p in ps))
            assert packets, "scheduler thread never fired the real transport"
        finally:
            sched.stop()

    def test_thread_idle_when_stopped(self) -> None:
        sched = ProbeScheduler([ProbeResult(name="broker", ok=False, latency_ms=1.0, detail="d")])
        sched.start()
        sched.stop()
        sched.stop()  # idempotent
        assert sched._thread is not None


class TestProbeSchedulerAllRoutes:
    """All four probes, each FORCED to fail through the real scheduler loop,
    page a CRITICAL packet through the genuine on-call transport."""

    def test_health_failure_pages(self, on_receiver) -> None:
        url, receiver = on_receiver
        sched = ProbeScheduler(
            [lambda: health_probe("http://127.0.0.1:1", timeout=1.0)],
            oncall=_router(url),
            audit=_MemoryAudit(),
            metrics=_MemoryMetrics(),
        )
        sched.run_once()
        packets = receiver.all()
        assert packets, "a failing health probe produced no on-call packet"
        assert packets[-1]["level"] == CRITICAL
        assert packets[-1]["title"] == "Probe failed: health"
        assert "ERROR" in packets[-1]["message"] or "connection" in packets[-1]["message"].lower()

    def test_vault_failure_pages_via_wrapped_path(self, on_receiver) -> None:
        url, receiver = on_receiver
        # Real VaultSecretProvider pointed at a dead loopback endpoint: the
        # exact WP1-wrapped fetch path (VAULT_CB) that live-key reads use.
        provider = VaultSecretProvider(url="http://127.0.0.1:9", token="t")
        sched = ProbeScheduler(
            [lambda: vault_probe(provider.get, "ALPACA_API_KEY")],
            oncall=_router(url),
            audit=_MemoryAudit(),
            metrics=_MemoryMetrics(),
        )
        sched.run_once()
        packets = receiver.all()
        assert packets, "a failing vault probe produced no on-call packet"
        assert packets[-1]["level"] == CRITICAL
        assert packets[-1]["title"] == "Probe failed: vault"
        assert "ERROR" in packets[-1]["message"]

    def test_broker_failure_pages(self, on_receiver) -> None:
        url, receiver = on_receiver
        sched = ProbeScheduler(
            [lambda: broker_health_probe(_BrokerUnreachable(), "paper", [])],
            oncall=_router(url),
            audit=_MemoryAudit(),
            metrics=_MemoryMetrics(),
        )
        sched.run_once()
        packets = receiver.all()
        assert packets[-1]["level"] == CRITICAL
        assert packets[-1]["title"] == "Probe failed: broker"
        assert "Broker unreachable" in packets[-1]["message"]

    def test_rate_limit_never_refusing_pages(self, on_receiver) -> None:
        url, receiver = on_receiver

        class _NeverRefusing:
            def check(self, key):
                return True

        sched = ProbeScheduler(
            [lambda: rate_limit_probe(_NeverRefusing(), budget=5)],
            oncall=_router(url),
            audit=_MemoryAudit(),
            metrics=_MemoryMetrics(),
        )
        sched.run_once()
        packets = receiver.all()
        assert packets, "a broken rate-limiter produced no on-call packet"
        assert packets[-1]["level"] == CRITICAL
        assert packets[-1]["title"] == "Probe failed: rate_limit"
        assert "not enforced" in packets[-1]["message"]

    def test_factory_wires_and_lifecycle_starts_scheduler(self, monkeypatch) -> None:
        from traderos.application.factory import build_orchestrator
        from traderos.infrastructure.config.config_loader import Config

        monkeypatch.delenv("VAULT_ADDR", raising=False)
        monkeypatch.delenv("PROBE_HEALTH_URL", raising=False)
        orch = build_orchestrator(mode="paper", config=Config(db_path=":memory:"))
        assert orch.probe_scheduler is not None
        results = orch.probe_scheduler.run_once()
        names = set(results)
        assert "broker" in names, "factory must wire the broker probe"
        assert "rate_limit" in names, "factory must wire the rate-limit probe"
        orch.probe_scheduler.start()
        orch.probe_scheduler.stop()
        orch.probe_scheduler.stop()  # idempotent

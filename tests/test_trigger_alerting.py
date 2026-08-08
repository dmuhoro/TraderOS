"""WP2 — real on-call transport fired by the REAL trigger/detection paths.

Not a standalone-notifier unit test: every case forces the actual production
detection path (Kill Switch / Reconciliation / Supervision) and asserts
delivery reached a real HTTP on-call transport with the correct severity. The
transport is a real loopback HTTP server (no fabricated credential — the same
honest-wire proof the A7 claim uses).

Triggers proven:
  1. KillSwitch trip           (live API kill-switch/engage path, CRITICAL)
  2. Reconciliation failure    (BrokerStateReconciliationService, CRITICAL)
  3. Unclean shutdown          (SupervisionService, CRITICAL)
  4. Low severity stays local  (INFO never leaves the wire)
"""

from __future__ import annotations

import json
import threading
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer

import pytest

from traderos.domain.services.broker_state_reconciliation_service import (
    BrokerStateReconciliationService,
)
from traderos.domain.services.notification_service import NotificationService
from traderos.infrastructure.notifiers.oncall_router import HttpOnCallTransport
from traderos.infrastructure.notifiers.oncall_router import OnCallRouter
from traderos.infrastructure.supervision import HeartbeatRecord
from traderos.infrastructure.supervision import JsonlHeartbeatStore
from traderos.infrastructure.supervision import SupervisionService

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


class _MemoryAudit:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str, str, str]] = []

    def record(self, action: str, actor: str, resource: str, detail: str = ""):
        self.entries.append((action, actor, resource, detail))


class _MemoryMetrics:
    def __init__(self) -> None:
        self.counters: dict[str, float] = {}

    def counter(self, name: str, delta: float = 1.0) -> float:
        self.counters[name] = self.counters.get(name, 0.0) + delta
        return self.counters[name]


@pytest.fixture()
def on_receiver():
    """Real loopback HTTP on-call transport capturing delivered packets.

    Yields (url, receiver); each test gets a fresh receiver bound to the
    capture server (reset in teardown so no cross-test leakage).
    """
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


def _notifications_with_oncall(url: str) -> NotificationService:
    router = OnCallRouter([HttpOnCallTransport(url, max_retries=1)])
    return NotificationService(notifier=None, oncall=router)


class _FailingBroker:
    def place_market_order(self, market_id, side, quantity, close_price=None, client_order_id=None):
        return None

    def place_limit_order(self, market_id, side, quantity, price, close_price=None):
        return None

    def cancel_order(self, order_id):
        return None

    def get_account_balance(self):
        return 10000.0

    def get_positions(self):
        raise RuntimeError("Broker unreachable")

    def get_open_orders(self):
        raise RuntimeError("Broker unreachable")


class _HealthyBroker:
    def place_market_order(self, market_id, side, quantity, close_price=None, client_order_id=None):
        return None

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


class TestReconciliationTriggerOnCall:
    def test_recon_failure_fires_critical_through_real_service(self, on_receiver) -> None:
        url, receiver = on_receiver
        notifications = _notifications_with_oncall(url)
        svc = BrokerStateReconciliationService(
            broker=_FailingBroker(),
            notifications=notifications,
        )
        result = svc.reconcile()
        assert result.failed
        packets = receiver.all()
        assert packets, "reconciliation failure produced no on-call packet"
        assert packets[-1]["level"] == CRITICAL
        assert packets[-1]["title"] == "Reconciliation Failure"

    def test_reconcile_success_fires_nothing(self, on_receiver) -> None:
        url, receiver = on_receiver
        notifications = _notifications_with_oncall(url)
        svc = BrokerStateReconciliationService(
            broker=_HealthyBroker(),
            notifications=notifications,
        )
        result = svc.reconcile()
        assert not result.failed
        assert receiver.all() == []

    def test_recon_failure_audited_even_without_transport(self) -> None:
        """Fail-closed: even with no transport, the failure is audited and
        counted through the real ports — never a silent drop."""
        audit = _MemoryAudit()
        metrics = _MemoryMetrics()
        svc = BrokerStateReconciliationService(
            broker=_FailingBroker(),
            notifications=None,
            audit=audit,
            metrics=metrics,
        )
        result = svc.reconcile()
        assert result.failed
        assert any(a == "reconciliation.failed" for a, _, _, _ in audit.entries)
        assert metrics.counters.get("reconciliation.failed", 0.0) >= 1.0


class TestSupervisionTriggerOnCall:
    def test_unclean_shutdown_fires_critical_through_real_detection(
        self, tmp_path, on_receiver
    ) -> None:
        url, receiver = on_receiver
        store = JsonlHeartbeatStore(tmp_path / "supervision.jsonl")
        notifications = _notifications_with_oncall(url)
        sup = SupervisionService(store=store, notifications=notifications)
        store.append(
            HeartbeatRecord(ts=datetime.now(UTC) - timedelta(minutes=5), pid=1, action="heartbeat")
        )
        sup._now = lambda: datetime.now(UTC)
        assert sup.check_unclean_shutdown() is True
        packets = receiver.all()
        assert packets, "unclean shutdown produced no on-call packet"
        assert packets[-1]["level"] == CRITICAL
        assert "Unclean Process Death" in packets[-1]["title"]

    def test_clean_shutdown_fires_nothing(self, tmp_path, on_receiver) -> None:
        url, receiver = on_receiver
        store = JsonlHeartbeatStore(tmp_path / "supervision.jsonl")
        notifications = _notifications_with_oncall(url)
        sup = SupervisionService(store=store, notifications=notifications)
        sup.heartbeat()
        sup.mark_clean_shutdown()
        assert sup.check_unclean_shutdown() is False
        assert receiver.all() == []


class TestSeverityRouting:
    def test_info_stays_local_never_routes_to_transport(self, on_receiver) -> None:
        url, receiver = on_receiver
        notifications = _notifications_with_oncall(url)
        notifications.info("heartbeat", "all good")
        assert receiver.all() == []


class TestKillSwitchTriggerThroughLiveApi:
    def test_kill_switch_trip_fires_critical_through_real_api_path(
        self, monkeypatch, on_receiver
    ) -> None:
        """The real kill-switch path (live API endpoint) must deliver a
        CRITICAL alert through the configured on-call transport."""
        url, receiver = on_receiver
        monkeypatch.setenv("ONCALL_WEBHOOK_URL", url)
        monkeypatch.setenv("ALPACA_API_KEY", "dummy")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "dummy")
        monkeypatch.setenv("TRADING_MODE", "paper")

        import importlib

        from traderos.infrastructure.auth import APIKeyAuthenticator
        from traderos.interfaces.api import security
        from traderos.interfaces.api import server

        importlib.reload(server)
        # Paper posture forces the fail-closed auth boundary (auth_required),
        # so supply an explicit admin key for the operator action.
        security.set_authenticator(APIKeyAuthenticator(admin_keys=("sprint-admin-1234567890",)))

        from fastapi.testclient import TestClient

        app = server.build_app()
        client = TestClient(app)
        headers = {"X-API-Key": "sprint-admin-1234567890"}
        before = len(receiver.all())
        resp = client.post("/v1/kill-switch/engage", headers=headers)
        assert resp.status_code == 200
        packets = receiver.all()
        new_packets = packets[before:]
        assert new_packets, "kill-switch trip produced no on-call packet"
        assert new_packets[-1]["level"] == CRITICAL
        assert new_packets[-1]["title"] == "Kill switch engaged"

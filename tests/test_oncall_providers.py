"""WP10 — PagerDuty + Slack provider transports fire against real HTTP wire.

Same honest-wire discipline as the A7 oncall tests: transports POST to a real
loopback HTTP server, and delivery is only counted on a 2xx, provider-native
ack. Proves the payload shapes (PagerDuty events/v2 envelope, Slack webhook
"ok") and the fail-closed no-credential construction guard.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer

import pytest

from traderos.domain.services.notification_service import NotificationLevel
from traderos.infrastructure.notifiers.oncall_router import OnCallDeliveryError
from traderos.infrastructure.notifiers.oncall_router import PagerDutyTransport
from traderos.infrastructure.notifiers.oncall_router import SlackTransport


class _Receiver:
    def __init__(self) -> None:
        self.bodies: list[bytes] = []
        self.status: list[int] = []
        self._lock = threading.Lock()

    def push(self, body: bytes, status: int) -> None:
        with self._lock:
            self.bodies.append(body)
            self.status.append(status)

    def all(self) -> list[bytes]:
        with self._lock:
            return list(self.bodies)


class _Control:
    reply_status: int = 200
    reply_body: bytes = b"{}"


class _Capture(BaseHTTPRequestHandler):
    control = _Control()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        _control_responses(body)
        status = _Capture.control.reply_status
        body_out = _Capture.control.reply_body
        self.send_response(status)
        self.send_header("Content-Length", str(len(body_out)))
        self.end_headers()
        self.wfile.write(body_out)

    def log_message(self, format: str, *args: object) -> None:
        pass


def _control_responses(body: bytes) -> None:
    with _lock:
        _received.append(body)


_received: list[bytes] = []
_lock = threading.Lock()


@pytest.fixture()
def loopback() -> str:
    _Capture.control = _Control()
    server = HTTPServer(("127.0.0.1", 0), _Capture)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}/"
    server.shutdown()


@pytest.fixture(autouse=True)
def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    with _lock:
        _received.clear()


class TestPagerDutyTransport:
    def test_requires_routing_key_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PAGERDUTY_ROUTING_KEY", raising=False)
        with pytest.raises(OnCallDeliveryError):
            PagerDutyTransport()

    def test_env_routing_key_wired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PAGERDUTY_ROUTING_KEY", "ab0123456789abcdef")
        t = PagerDutyTransport()
        assert t.routing_key == "ab0123456789abcdef"

    def test_critical_delivered_with_events_v2_envelope(self, loopback: str) -> None:
        t = PagerDutyTransport("ab0123456789abcdef", base_url=loopback)
        t.deliver(
            "Kill switch engaged",
            "flatten forced",
            NotificationLevel.CRITICAL,
            {"dedup_key": "ks-1"},
        )
        with _lock:
            assert len(_received) == 1
            body = json.loads(_received[0])
        assert body["routing_key"] == "ab0123456789abcdef"
        assert body["event_action"] == "trigger"
        assert body["dedup_key"] == "ks-1"
        assert body["payload"]["severity"] == "critical"
        assert body["payload"]["summary"] == "Kill switch engaged"

    def test_non_2xx_raises_delivery_error(self, loopback: str) -> None:
        _Capture.control.reply_status = 400
        _Capture.control.reply_body = b'{"status":"bad request"}'
        t = PagerDutyTransport("ab0123456789abcdef", base_url=loopback)
        with pytest.raises(OnCallDeliveryError):
            t.deliver("t", "m", NotificationLevel.ERROR, {})

    def test_api_status_rejection_raises(self, loopback: str) -> None:
        _Capture.control.reply_status = 200
        _Capture.control.reply_body = b'{"status":"invalid_event"}'
        t = PagerDutyTransport("ab0123456789abcdef", base_url=loopback)
        with pytest.raises(OnCallDeliveryError):
            t.deliver("t", "m", NotificationLevel.ERROR, {})


class TestSlackTransport:
    def test_requires_webhook_url_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        with pytest.raises(OnCallDeliveryError):
            SlackTransport()

    def test_critical_delivered_with_slack_payload(self, loopback: str) -> None:
        _Capture.control.reply_body = b'{"ok": true}'
        t = SlackTransport(loopback)
        t.deliver(
            "Kill switch engaged",
            "flatten forced",
            NotificationLevel.CRITICAL,
            {"dedup_key": "ks-1"},
        )
        with _lock:
            assert len(_received) == 1
            body = json.loads(_received[0])
        assert "Kill switch engaged" in body["text"]
        assert "flatten forced" in body["text"]
        assert "critical" in body["text"]

    def test_slack_reject_ok_false_raises(self, loopback: str) -> None:
        _Capture.control.reply_status = 200
        _Capture.control.reply_body = b'{"ok": false, "error": "invalid_payload"}'
        t = SlackTransport(loopback)
        with pytest.raises(OnCallDeliveryError):
            t.deliver("t", "m", NotificationLevel.ERROR, {})

    def test_non_2xx_raises(self, loopback: str) -> None:
        _Capture.control.reply_status = 500
        t = SlackTransport(loopback)
        with pytest.raises(OnCallDeliveryError):
            t.deliver("t", "m", NotificationLevel.ERROR, {})


class TestFactoryWiring:
    def test_factory_fans_out_to_configured_providers(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setenv("PAGERDUTY_ROUTING_KEY", "ab0123456789abcdef")
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        monkeypatch.delenv("ONCALL_WEBHOOK_URL", raising=False)
        monkeypatch.setenv("DB_PATH", str(tmp_path / "wp10.db"))

        from traderos.application import factory as factory_mod

        orch = factory_mod.build_orchestrator()
        assert orch.notifications is not None
        oncall = orch.notifications.oncall
        assert oncall is not None
        assert len(oncall.transports) == 1
        assert isinstance(oncall.transports[0], PagerDutyTransport)

    def test_factory_no_provider_leaves_oncall_unset(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.delenv("PAGERDUTY_ROUTING_KEY", raising=False)
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        monkeypatch.delenv("ONCALL_WEBHOOK_URL", raising=False)
        monkeypatch.setenv("DB_PATH", str(tmp_path / "wp10-none.db"))

        from traderos.application import factory as factory_mod

        orch = factory_mod.build_orchestrator()
        assert orch.notifications is not None
        assert orch.notifications.oncall is None

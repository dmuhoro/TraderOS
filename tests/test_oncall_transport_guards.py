"""Defensive branches in on-call transports (A7/WP10) a real HTTP wire can't reach.

Real ``urllib.request.urlopen`` raises ``HTTPError`` on a non-2xx response before
the transport can read ``resp.status``, so the explicit non-2xx ``RuntimeError``
guards, the JSON-fallback branches and the platform-unavailable guard are proven
here with a fake ``urlopen``. Every delivery still must either succeed or raise
``OnCallDeliveryError`` — never silently drop.
"""

from __future__ import annotations

import json

import pytest

from traderos.domain.exceptions import ServiceError
from traderos.domain.services.notification_service import NotificationLevel
from traderos.infrastructure.notifiers import oncall_router as ocr
from traderos.infrastructure.notifiers.oncall_router import HttpOnCallTransport
from traderos.infrastructure.notifiers.oncall_router import OnCallDeliveryError
from traderos.infrastructure.notifiers.oncall_router import PagerDutyTransport
from traderos.infrastructure.notifiers.oncall_router import SlackTransport


class _FakeResponse:
    def __init__(self, *, status: int = 200, body: bytes = b"{}") -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body


class _FakeRequest:
    def __init__(
        self, url: str, data: bytes | None = None, headers: dict[str, str] | None = None
    ) -> None:
        self.url = url
        self.data = data
        self.headers = headers or {}

    def get_header(self, name: str) -> str | None:
        return self.headers.get(name)


def _patch_urlopen(
    monkeypatch: pytest.MonkeyPatch, *, status: int = 200, body: bytes = b"{}"
) -> list[object]:
    captured: list[object] = []

    def fake_open(req: object, timeout: float = 5.0) -> _FakeResponse:
        captured.append(req)
        return _FakeResponse(status=status, body=body)

    monkeypatch.setattr(ocr, "urlopen", fake_open)
    monkeypatch.setattr(ocr, "Request", _FakeRequest)
    return captured


def _patch_retry_to_null_urlopen(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_retry(fn: object, **kwargs: object) -> object:
        original = ocr.urlopen
        ocr.urlopen = None
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            raise ServiceError("Operation failed") from exc
        finally:
            ocr.urlopen = original

    monkeypatch.setattr(ocr, "retry_with_backoff", fake_retry)


@pytest.fixture()
def critical() -> tuple[str, str, NotificationLevel, dict[str, str]]:
    return ("kill switch", "flatten forced", NotificationLevel.CRITICAL, {"dedup_key": "ks-1"})


class TestHttpOnCallTransportGuards:
    def test_unavailable_platform_raises(self, monkeypatch: pytest.MonkeyPatch, critical) -> None:
        monkeypatch.setattr(ocr, "_has_urlopen", False)
        t = HttpOnCallTransport("https://example.invalid/oncall")
        with pytest.raises(OnCallDeliveryError, match="unavailable"):
            t.deliver(*critical)

    def test_bearer_token_sent_on_headers(self, monkeypatch: pytest.MonkeyPatch, critical) -> None:
        captured = _patch_urlopen(monkeypatch, status=200)
        t = HttpOnCallTransport(
            "https://example.invalid/oncall", bearer_token="tok-123", max_retries=0
        )
        t.deliver(*critical)
        assert captured[0].get_header("Authorization") == "Bearer tok-123"

    def test_non_2xx_raises_delivery_error(self, monkeypatch: pytest.MonkeyPatch, critical) -> None:
        _patch_urlopen(monkeypatch, status=500, body=b"oops")
        t = HttpOnCallTransport("https://example.invalid/oncall", max_retries=0)
        with pytest.raises(OnCallDeliveryError, match="not delivered"):
            t.deliver(*critical)

    def test_urlopen_none_in_closure_raises(
        self, monkeypatch: pytest.MonkeyPatch, critical
    ) -> None:
        _patch_retry_to_null_urlopen(monkeypatch)
        t = HttpOnCallTransport("https://example.invalid/oncall", max_retries=0)
        with pytest.raises(OnCallDeliveryError, match="not delivered"):
            t.deliver(*critical)


class TestPagerDutyTransportGuards:
    def test_unavailable_platform_raises(self, monkeypatch: pytest.MonkeyPatch, critical) -> None:
        monkeypatch.setattr(ocr, "_has_urlopen", False)
        t = PagerDutyTransport("ab0123456789abcdef", max_retries=0)
        with pytest.raises(OnCallDeliveryError, match="unavailable"):
            t.deliver(*critical)

    def test_non_2xx_raises_delivery_error(self, monkeypatch: pytest.MonkeyPatch, critical) -> None:
        _patch_urlopen(monkeypatch, status=400, body=b'{"status":"bad request"}')
        t = PagerDutyTransport("ab0123456789abcdef", max_retries=0)
        with pytest.raises(OnCallDeliveryError, match="not delivered"):
            t.deliver(*critical)

    def test_bad_json_body_is_ignored(self, monkeypatch: pytest.MonkeyPatch, critical) -> None:
        _patch_urlopen(monkeypatch, status=200, body=b"this is not json")
        t = PagerDutyTransport("ab0123456789abcdef", max_retries=0)
        t.deliver(*critical)

    def test_status_rejection_raises(self, monkeypatch: pytest.MonkeyPatch, critical) -> None:
        _patch_urlopen(monkeypatch, status=200, body=b'{"status":"invalid_event"}')
        t = PagerDutyTransport("ab0123456789abcdef", max_retries=0)
        with pytest.raises(OnCallDeliveryError, match="not delivered"):
            t.deliver(*critical)


class TestSlackTransportGuards:
    def test_unavailable_platform_raises(self, monkeypatch: pytest.MonkeyPatch, critical) -> None:
        monkeypatch.setattr(ocr, "_has_urlopen", False)
        t = SlackTransport("https://hooks.slack.example/in")
        with pytest.raises(OnCallDeliveryError, match="unavailable"):
            t.deliver(*critical)

    def test_missing_url_after_init_raises(self, monkeypatch: pytest.MonkeyPatch, critical) -> None:
        t = SlackTransport("https://hooks.slack.example/in", max_retries=0)
        monkeypatch.setattr(t, "webhook_url", "")
        with pytest.raises(OnCallDeliveryError, match="SLACK_WEBHOOK_URL"):
            t.deliver(*critical)

    def test_channel_attached_when_configured(
        self, monkeypatch: pytest.MonkeyPatch, critical
    ) -> None:
        monkeypatch.setenv("SLACK_CHANNEL", "#ops-alerts")
        captured = _patch_urlopen(monkeypatch, status=200, body=b'{"ok": true}')
        t = SlackTransport("https://hooks.slack.example/in", max_retries=0)
        t.deliver(*critical)
        payload = json.loads(captured[0].data)
        assert payload["channel"] == "#ops-alerts"

    def test_non_2xx_raises_delivery_error(self, monkeypatch: pytest.MonkeyPatch, critical) -> None:
        _patch_urlopen(monkeypatch, status=500, body=b"oops")
        t = SlackTransport("https://hooks.slack.example/in", max_retries=0)
        with pytest.raises(OnCallDeliveryError, match="not delivered"):
            t.deliver(*critical)

    def test_bad_json_body_is_ignored(self, monkeypatch: pytest.MonkeyPatch, critical) -> None:
        _patch_urlopen(monkeypatch, status=200, body=b"definitely not json")
        t = SlackTransport("https://hooks.slack.example/in", max_retries=0)
        t.deliver(*critical)

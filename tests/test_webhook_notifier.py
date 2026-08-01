from __future__ import annotations

import json
import logging
from unittest.mock import patch

from traderos.infrastructure.notifiers import webhook_notifier
from traderos.infrastructure.notifiers.webhook_notifier import WebhookNotifier

_PAYLOAD = {"level": "info", "title": "t", "message": "m", "metadata": {}}


def _send(webhook_url: str | None = "https://hooks.example.test/abc") -> None:
    WebhookNotifier(webhook_url=webhook_url).send_notification(
        title="t", message="m", level="info", metadata={}
    )


class TestWebhookNotifier:
    def test_sends_json_post_to_configured_url(self) -> None:
        with patch.object(webhook_notifier, "urlopen") as urlopen:
            _send()
        assert urlopen.call_count == 1
        args, kwargs = urlopen.call_args
        (request,) = args
        assert request.full_url == "https://hooks.example.test/abc"
        assert request.get_header("Content-type") == "application/json"
        assert json.loads(request.data) == _PAYLOAD
        assert kwargs.get("timeout") == 5

    def test_no_url_logs_instead_of_posting(self, caplog) -> None:
        caplog.set_level(logging.INFO, logger="traderos.infrastructure.notifiers.webhook_notifier")
        with patch.object(webhook_notifier, "urlopen") as urlopen:
            _send(webhook_url="")
        assert urlopen.call_count == 0
        assert any("NOTIFICATION_WEBHOOK" in r.message for r in caplog.records)

    def test_urllib_unavailable_logs_warning(self, caplog) -> None:
        with patch.object(webhook_notifier, "_has_urlopen", False):
            _send()
        assert any("Webhook unavailable" in r.message for r in caplog.records)

    def test_failed_post_is_swallowed_after_retries(self, caplog) -> None:
        from urllib.error import URLError

        with (
            patch.object(webhook_notifier, "urlopen", side_effect=URLError("boom")),
            patch("traderos.infrastructure.retry.time.sleep"),
        ):
            _send()
        assert any("Webhook POST failed" in r.message for r in caplog.records)

    def test_oserror_failure_is_swallowed(self, caplog) -> None:
        with (
            patch.object(webhook_notifier, "urlopen", side_effect=OSError("conn")),
            patch("traderos.infrastructure.retry.time.sleep"),
        ):
            _send()
        assert any("Webhook POST failed" in r.message for r in caplog.records)

    def test_webhook_url_reads_from_environment(self, monkeypatch) -> None:
        monkeypatch.setenv("WEBHOOK_URL", "https://hooks.example.test/env")
        notifier = WebhookNotifier()
        assert notifier.webhook_url == "https://hooks.example.test/env"

    def test_success_returns_without_retry(self) -> None:
        with (
            patch.object(webhook_notifier, "urlopen", return_value=object()),
            patch("traderos.infrastructure.retry.time.sleep") as sleep,
        ):
            _send()
        sleep.assert_not_called()

from __future__ import annotations

import json
import logging
import os
from typing import Any

from traderos.infrastructure.retry import retry_with_backoff

try:
    from urllib.error import URLError as _URLError
    from urllib.request import Request
    from urllib.request import urlopen

    _has_urlopen = True
except ImportError:
    _has_urlopen = False
    _URLError = OSError
    Request = None
    urlopen = None


log = logging.getLogger(__name__)


class WebhookNotifier:
    def __init__(self, webhook_url: str | None = None) -> None:
        self.webhook_url = webhook_url or os.getenv("WEBHOOK_URL", "")

    def send_notification(
        self,
        title: str,
        message: str,
        level: str,
        metadata: dict[str, str | float | int | None],
    ) -> None:
        if not _has_urlopen or urlopen is None or Request is None:
            log.warning("Webhook unavailable (urllib not available)")
            return

        payload = json.dumps(
            {
                "level": level,
                "title": title,
                "message": message,
                "metadata": metadata,
            }
        ).encode()

        if not self.webhook_url:
            log.info("NOTIFICATION_WEBHOOK (no URL configured): %s", payload.decode())
            return

        req = Request(
            self.webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )

        def _do_webhook() -> Any:
            if urlopen is None:
                raise RuntimeError("urllib.request.urlopen not available")
            return urlopen(req, timeout=5)

        try:
            retry_with_backoff(_do_webhook, max_retries=2)
        except (_URLError, OSError):
            log.warning("Webhook POST failed")

from __future__ import annotations

import json
import logging
import os
from typing import Any
from typing import Protocol

from traderos.domain.exceptions import ServiceError
from traderos.domain.services.notification_service import NotificationLevel
from traderos.infrastructure.retry import retry_with_backoff

try:
    from urllib.request import Request
    from urllib.request import urlopen

    _has_urlopen = True
except ImportError:  # pragma: no cover
    _has_urlopen = False
    Request = None
    urlopen = None

log = logging.getLogger(__name__)

# Severity-routed on-call alert transport (A7).
#
# Today a CRITICAL alert can be silently dropped: ``WebhookNotifier`` swallows
# delivery failures with a warning and ``NotificationService`` skips the webhook
# when no notifier is configured, with no acknowledgment and no audit/metric
# trace. A7 closes that: alerts route by severity to >=1 external transport, a
# delivery is only "delivered" on an HTTP 2xx ack, and a failed delivery of a
# CRITICAL alert is recorded (audit + metric) and surfaced, never silent.
#
#   - ``OnCallTransport`` protocol: ``deliver(title, message, level, metadata)``
#     raises on a non-2xx / network failure. The *caller* decides what a raise
#     means; the router below treats any transport failure as not-delivered.
#   - ``HttpOnCallTransport``: one external webhook/PagerDuty/Slack-style URL,
#     retried with backoff, 2xx required, auth via optional bearer token.
#   - ``OnCallRouter``: the severity-routing seam. Only events at/above
#     ``min_severity`` are routed externally; lower severities stay local. Every
#     routing outcome (delivered / delivery-failed) is written to the real
#     audit trail and a metric counter. On a CRITICAL delivery failure it raises
#     ``OnCallDeliveryError`` so the caller cannot mistake failure for success.

_SEVERITY_RANK = {
    NotificationLevel.INFO: 0,
    NotificationLevel.WARNING: 1,
    NotificationLevel.ERROR: 2,
    NotificationLevel.CRITICAL: 3,
}


class OnCallDeliveryError(RuntimeError):
    """A routed CRITICAL alert could not be delivered to any transport."""


class OnCallTransport(Protocol):
    def deliver(
        self,
        title: str,
        message: str,
        level: NotificationLevel,
        metadata: dict[str, str | float | int | None],
    ) -> None: ...


class HttpOnCallTransport:
    """One external webhook URL; delivery is only success on HTTP 2xx.

    Retried with backoff (default 3 attempts). Non-2xx responses and network
    errors raise, so a false "delivered" is impossible.
    """

    def __init__(
        self,
        url: str,
        *,
        bearer_token: str | None = None,
        max_retries: int = 3,
        timeout: float = 5.0,
    ) -> None:
        self.url = url
        self.bearer_token = bearer_token or os.getenv("ONCALL_BEARER_TOKEN", "") or None
        self.max_retries = max_retries
        self.timeout = timeout

    def deliver(
        self,
        title: str,
        message: str,
        level: NotificationLevel,
        metadata: dict[str, str | float | int | None],
    ) -> None:
        if not _has_urlopen or urlopen is None or Request is None:
            raise OnCallDeliveryError("urllib.request.urlopen unavailable on this platform")
        payload = json.dumps(
            {
                "level": level.value,
                "title": title,
                "message": message,
                "metadata": metadata,
                "source": "traderos-oncall",
            }
        ).encode()
        headers = {"Content-Type": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        req = Request(self.url, data=payload, headers=headers)

        def _post() -> Any:
            if urlopen is None:
                raise RuntimeError("urllib.request.urlopen not available")
            resp = urlopen(req, timeout=self.timeout)
            status = getattr(resp, "status", getattr(resp, "getcode", lambda: 200)())
            if not (200 <= int(status) < 300):
                raise RuntimeError(f"on-call transport returned HTTP {status}")
            return resp

        try:
            retry_with_backoff(_post, max_retries=self.max_retries, base_delay=0.2, max_delay=2.0)
        except ServiceError as exc:
            raise OnCallDeliveryError(
                f"on-call transport {self.url!r} not delivered after {self.max_retries + 1} "
                f"attempts: {exc}"
            ) from exc


class OnCallRouter:
    """Severity-routed fan-out to >=1 external on-call transport.

    Every attempt is audited (``oncall.delivered`` / ``oncall.delivery_failed``)
    and counted (``oncall.delivered`` / ``oncall.delivery_failed``). Routing a
    CRITICAL alert with zero successful deliveries raises
    ``OnCallDeliveryError`` — failure is loud, never a silent drop.
    """

    def __init__(
        self,
        transports: list[OnCallTransport],
        *,
        min_severity: NotificationLevel = NotificationLevel.CRITICAL,
        audit: Any | None = None,
        metrics: Any | None = None,
    ) -> None:
        self.transports = list(transports)
        self.min_severity = min_severity
        self._audit = audit
        self._metrics = metrics

    def route(
        self,
        level: NotificationLevel,
        title: str,
        message: str,
        metadata: dict[str, str | float | int | None] | None = None,
    ) -> bool:
        metadata = metadata or {}
        if _SEVERITY_RANK[level] < _SEVERITY_RANK[self.min_severity]:
            return True  # below threshold: stays local by design, not a failure
        delivered = False
        for transport in self.transports:
            try:
                transport.deliver(title, message, level, metadata)
                delivered = True
            except OnCallDeliveryError as exc:
                log.warning("on-call transport failed: %s", exc)
        if delivered:
            if self._audit:
                self._audit.record(
                    "oncall.delivered", "oncall-router", title, f"level={level.value}"
                )
            if self._metrics:
                self._metrics.counter("oncall.delivered", 1.0)
            return True
        reason = f"no on-call transport delivered level={level.value} alert {title!r}"
        if self._audit:
            self._audit.record("oncall.delivery_failed", "oncall-router", title, reason)
        if self._metrics:
            self._metrics.counter("oncall.delivery_failed", 1.0)
        if level == NotificationLevel.CRITICAL:
            raise OnCallDeliveryError(reason)
        return False

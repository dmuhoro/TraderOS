"""Global fatal-exception rail: broadcast diagnostics, flatten, freeze (G-05).

Sprint 3.4: when a critical, *unexpected* exception escapes the trading loop,
the process must not limp on with unknown capital exposure. This handler,
installed as ``sys.excepthook`` at the daemon boundary, (1) broadcasts a
detailed diagnostic payload over the real notification seam (console + webhook
+ on-call as configured), (2) attempts an exactly-once portfolio flatten
through the true broker submission path, and (3) terminates the process with
``sys.exit(1)``.

Termination happens **regardless** of whether alerting or flattening succeeded:
failure to alert or close must never leave a half-alive trading process — it
always dies, forcing a human to investigate before capital is touched again
(fail closed, never fail open).
"""

from __future__ import annotations

import os
import sys
import traceback
from collections.abc import Callable
from datetime import UTC
from datetime import datetime
from typing import Any
from typing import NoReturn
from typing import Self

from traderos.domain.ports import AuditPort
from traderos.domain.ports import MetricsPort
from traderos.domain.services.flatten_service import FlattenService
from traderos.domain.services.notification_service import NotificationService


class FatalExceptionHandler:
    """Installable ``sys.excepthook`` that freezes the trading process safely."""

    def __init__(
        self,
        notifications: NotificationService,
        flatten_service: FlattenService | None = None,
        audit: AuditPort | None = None,
        metrics: MetricsPort | None = None,
        *,
        mode: str = "unknown",
        exit_fn: Callable[[int], NoReturn] | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._notifications = notifications
        self._flatten_service = flatten_service
        self._audit = audit
        self._metrics = metrics
        self._mode = mode
        self._exit = exit_fn or sys.exit
        self._now = now_fn or (lambda: datetime.now(UTC))
        self._previous_hook: Any | None = None

    def install(self) -> None:
        """Install as ``sys.excepthook`` (must be restored via ``uninstall``)."""
        self._previous_hook = sys.excepthook
        sys.excepthook = self.handle

    def uninstall(self) -> None:
        if self._previous_hook is not None:
            sys.excepthook = self._previous_hook
            self._previous_hook = None

    def __enter__(self) -> Self:
        self.install()
        return self

    def __exit__(self, *_: object) -> None:
        self.uninstall()

    def handle(self, exc_type: type[BaseException], exc_value: BaseException, exc_tb: Any) -> None:
        """``sys.excepthook`` entrypoint: freeze the process, never return."""
        try:
            self._freeze(exc_type, exc_value, exc_tb)
        except Exception:  # noqa: S110, BLE001  # pragma: no cover
            pass  # pragma: no cover - unreachable; _freeze guards each step
        finally:
            self._exit(1)

    def _freeze(self, exc_type: type[BaseException], exc_value: BaseException, exc_tb: Any) -> None:
        detail = f"{exc_type.__name__}: {exc_value}"
        trace = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            self._notifications.critical(
                "Fatal Error — Trading Frozen",
                detail,
                metadata={
                    "exception": detail,
                    "mode": self._mode,
                    "pid": int(os.getpid()),
                    "traceback": trace,
                },
            )
        except Exception:  # noqa: S110, BLE001 — an alert failure must not skip the flatten
            pass
        if self._audit:
            try:
                self._audit.record("fatal.exception", "system", "daemon", trace)
            except Exception:  # noqa: S110, BLE001
                pass
        if self._metrics:
            try:
                self._metrics.counter("fatal.exception", 1.0)
            except Exception:  # noqa: S110, BLE001
                pass
        if self._flatten_service is not None:
            try:
                result = self._flatten_service.flatten(
                    reason=f"fatal exception: {exc_type.__name__}"
                )
                self._notifications.critical(
                    "Fatal Flatten Result",
                    f"{result.close_orders} positions closed, " f"{result.failed_orders} failed",
                    metadata={
                        "close_orders": int(result.close_orders),
                        "failed_orders": int(result.failed_orders),
                        "errors": "; ".join(result.errors),
                    },
                )
            except Exception:  # noqa: S110, BLE001 — a broken flatten must never prevent exiting
                pass

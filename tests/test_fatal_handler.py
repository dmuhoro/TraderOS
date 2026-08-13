"""Gap 3 / Sprint 3.4: fatal-exception freeze rail.

Proves the "critical exception → broadcast diagnostics → flatten →
``sys.exit(1)``" path: the handler always terminates the process even when the
alert or the flatten itself fails (fail closed), restores the previous
``sys.excepthook``, and routes the critical through the webhook rail when
enabled (G-05).
"""

from __future__ import annotations

import sys
from datetime import UTC
from datetime import datetime

import pytest

from traderos.domain.services.flatten_service import FlattenResult
from traderos.domain.services.notification_service import NotificationService
from traderos.infrastructure.fatal_handler import FatalExceptionHandler


class _RecordingNotifier:
    def __init__(self, *, raise_on_send: bool = False) -> None:
        self.calls: list[tuple] = []
        self._raise_on_send = raise_on_send

    def send_notification(self, title: str, message: str, level: str, metadata: dict) -> None:
        if self._raise_on_send:
            raise RuntimeError("webhook down")
        self.calls.append((title, message, level, metadata))


class _RecordingFlatten:
    def __init__(self, *, raise_on_flatten: bool = False) -> None:
        self.reasons: list[str] = []
        self._raise_on_flatten = raise_on_flatten

    def flatten(self, reason: str) -> FlattenResult:
        self.reasons.append(reason)
        if self._raise_on_flatten:
            raise RuntimeError("flatten boom")
        return FlattenResult(flattened=True, close_orders=2, failed_orders=0)


def _handler(*, raise_send: bool = False, raise_flatten: bool = False):
    notifier = _RecordingNotifier(raise_on_send=raise_send)
    notifications = NotificationService(notifier=notifier, webhook_on_critical=True)
    flatten = _RecordingFlatten(raise_on_flatten=raise_flatten)
    exits: list[int] = []

    def exit_fn(code: int) -> None:
        exits.append(code)
        raise SystemExit(code)

    handler = FatalExceptionHandler(
        notifications,
        flatten,
        mode="paper",
        exit_fn=exit_fn,
        now_fn=lambda: datetime(2026, 8, 12, tzinfo=UTC),
    )
    return handler, notifier, flatten, exits


def test_handle_broadcasts_flattens_and_exits() -> None:
    handler, notifier, flatten, exits = _handler()
    with pytest.raises(SystemExit):
        handler.handle(ValueError, ValueError("boom"), None)
    assert exits == [1]
    assert flatten.reasons == ["fatal exception: ValueError"]
    titles = [c[0] for c in notifier.calls]
    assert "Fatal Error — Trading Frozen" in titles
    assert "Fatal Flatten Result" in titles
    frozen = next(c for c in notifier.calls if c[0] == "Fatal Error — Trading Frozen")
    assert frozen[2] == "CRITICAL"
    assert "boom" in frozen[1]
    assert frozen[3]["mode"] == "paper"


def test_exits_even_when_flatten_fails() -> None:
    handler, notifier, flatten, exits = _handler(raise_flatten=True)
    with pytest.raises(SystemExit):
        handler.handle(RuntimeError, RuntimeError("boom"), None)
    assert exits == [1]
    assert flatten.reasons == ["fatal exception: RuntimeError"]
    titles = [c[0] for c in notifier.calls]
    assert "Fatal Error — Trading Frozen" in titles
    assert "Fatal Flatten Result" not in titles


def test_exits_even_when_notification_fails() -> None:
    handler, _, flatten, exits = _handler(raise_send=True)
    with pytest.raises(SystemExit):
        handler.handle(RuntimeError, RuntimeError("boom"), None)
    assert exits == [1]
    assert flatten.reasons == ["fatal exception: RuntimeError"]


def test_handle_without_flatten_service_still_exits() -> None:
    notifier = _RecordingNotifier()
    notifications = NotificationService(notifier=notifier, webhook_on_critical=True)
    exits: list[int] = []

    def exit_fn(code: int) -> None:
        exits.append(code)
        raise SystemExit(code)

    handler = FatalExceptionHandler(notifications, mode="live", exit_fn=exit_fn)
    with pytest.raises(SystemExit):
        handler.handle(RuntimeError, RuntimeError("boom"), None)
    assert exits == [1]
    assert notifier.calls


def test_audit_and_metrics_failures_do_not_skip_flatten() -> None:
    class _BrokenAudit:
        def record(self, action: str, actor: str, subject: str, detail: str) -> None:
            raise RuntimeError("audit down")

    class _BrokenMetrics:
        def counter(self, name: str, value: float) -> None:
            raise RuntimeError("metrics down")

    notifier = _RecordingNotifier()
    notifications = NotificationService(notifier=notifier, webhook_on_critical=True)
    flatten = _RecordingFlatten()
    exits: list[int] = []

    def exit_fn(code: int) -> None:
        exits.append(code)
        raise SystemExit(code)

    handler = FatalExceptionHandler(
        notifications,
        flatten,
        audit=_BrokenAudit(),  # type: ignore[arg-type]
        metrics=_BrokenMetrics(),  # type: ignore[arg-type]
        exit_fn=exit_fn,
    )
    with pytest.raises(SystemExit):
        handler.handle(ValueError, ValueError("boom"), None)
    assert exits == [1]
    assert flatten.reasons == ["fatal exception: ValueError"]
    assert "Fatal Flatten Result" in [c[0] for c in notifier.calls]


def test_install_restores_previous_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    previous = sys.excepthook
    handler, *_ = _handler()
    handler.install()
    assert sys.excepthook == handler.handle
    handler.uninstall()
    assert sys.excepthook is previous


def test_context_manager_installs_and_uninstalls(monkeypatch: pytest.MonkeyPatch) -> None:
    previous = sys.excepthook
    handler, *_ = _handler()
    with handler:
        assert sys.excepthook == handler.handle
    assert sys.excepthook is previous


def test_uninstall_without_install_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    previous = sys.excepthook
    handler, *_ = _handler()
    handler.uninstall()
    assert sys.excepthook is previous


def test_audit_and_metrics_recorded() -> None:
    class _Audit:
        def __init__(self) -> None:
            self.entries: list[str] = []

        def record(self, action: str, actor: str, subject: str, detail: str) -> None:
            self.entries.append(f"{action}:{subject}")

    class _Metrics:
        def __init__(self) -> None:
            self.counters: dict[str, float] = {}

        def counter(self, name: str, value: float) -> None:
            self.counters[name] = self.counters.get(name, 0.0) + value

    audit, metrics = _Audit(), _Metrics()
    notifier = _RecordingNotifier()
    notifications = NotificationService(notifier=notifier, webhook_on_critical=True)
    flatten = _RecordingFlatten()

    def exit_fn(code: int) -> None:
        raise SystemExit(code)

    handler = FatalExceptionHandler(
        notifications, flatten, audit=audit, metrics=metrics, exit_fn=exit_fn
    )
    with pytest.raises(SystemExit):
        handler.handle(ValueError, ValueError("boom"), None)
    assert audit.entries == ["fatal.exception:daemon"]
    assert metrics.counters["fatal.exception"] == 1.0

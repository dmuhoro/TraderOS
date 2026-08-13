from __future__ import annotations

from traderos.domain.services.notification_service import NotificationChannel
from traderos.domain.services.notification_service import NotificationLevel
from traderos.domain.services.notification_service import NotificationService


class _RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def send_notification(
        self, title: str, message: str, level: str, metadata: dict | None = None
    ) -> None:
        self.calls.append((title, message, level))


class _RecordingOncall:
    def __init__(self) -> None:
        self.routes: list[tuple] = []

    def route(self, level: NotificationLevel, title: str, message: str, metadata: dict) -> None:
        self.routes.append((level, title, message, metadata))


class TestNotificationService:
    def test_send_info(self) -> None:
        svc = NotificationService()
        event = svc.info("Test Title", "Test Message")
        assert event.level == NotificationLevel.INFO
        assert event.title == "Test Title"
        assert event.message == "Test Message"
        assert event.channel == NotificationChannel.CONSOLE

    def test_send_warning(self) -> None:
        svc = NotificationService()
        event = svc.warning("Warning Title")
        assert event.level == NotificationLevel.WARNING

    def test_send_error(self) -> None:
        svc = NotificationService()
        event = svc.error("Error Title")
        assert event.level == NotificationLevel.ERROR

    def test_send_critical(self) -> None:
        svc = NotificationService()
        event = svc.critical("Critical Title")
        assert event.level == NotificationLevel.CRITICAL

    def test_send_file_channel(self) -> None:
        svc = NotificationService()
        event = svc.info("File Test", channel=NotificationChannel.FILE)
        assert event.channel == NotificationChannel.FILE

    def test_send_webhook_channel(self) -> None:
        svc = NotificationService()
        event = svc.info("Webhook Test", channel=NotificationChannel.WEBHOOK)
        assert event.channel == NotificationChannel.WEBHOOK

    def test_send_with_metadata(self) -> None:
        svc = NotificationService()
        event = svc.send(
            NotificationLevel.WARNING,
            "Alert",
            "Something happened",
            metadata={"cpu": 0.95, "retries": 3},
        )
        assert event.metadata["cpu"] == 0.95
        assert event.metadata["retries"] == 3

    def test_multiple_channels_uses_first(self) -> None:
        svc = NotificationService(channels={NotificationChannel.CONSOLE, NotificationChannel.FILE})
        event = svc.info("Multi", "Message")
        assert event.channel in {NotificationChannel.CONSOLE, NotificationChannel.FILE}

    def test_webhook_on_critical_fans_out(self) -> None:
        recorder = _RecordingNotifier()
        svc = NotificationService(notifier=recorder, webhook_on_critical=True)
        event = svc.critical("Kill Trip", "flatten forced")
        assert event.channel == NotificationChannel.CONSOLE
        assert len(recorder.calls) == 1
        assert recorder.calls[0][0] == "Kill Trip"
        assert recorder.calls[0][2] == "CRITICAL"

    def test_webhook_on_critical_does_not_fire_for_info(self) -> None:
        recorder = _RecordingNotifier()
        svc = NotificationService(notifier=recorder, webhook_on_critical=True)
        svc.info("Just Info", "message")
        assert recorder.calls == []

    def test_webhook_on_critical_false_fires_nothing(self) -> None:
        recorder = _RecordingNotifier()
        svc = NotificationService(notifier=recorder, webhook_on_critical=False)
        svc.critical("Kill Trip", "flatten forced")
        assert recorder.calls == []

    def test_webhook_channel_direct_does_not_double_send(self) -> None:
        recorder = _RecordingNotifier()
        svc = NotificationService(notifier=recorder, webhook_on_critical=True)
        svc.critical("Direct", "message", channel=NotificationChannel.WEBHOOK)
        assert len(recorder.calls) == 1
        assert recorder.calls[0][0] == "Direct"

    def test_oncall_routes_with_metadata(self) -> None:
        oncall = _RecordingOncall()
        svc = NotificationService(oncall=oncall)
        svc.error("Alarm", "disk full", metadata={"usage": 0.97})
        assert len(oncall.routes) == 1
        assert oncall.routes[0][0] == NotificationLevel.ERROR
        assert oncall.routes[0][1] == "Alarm"
        assert oncall.routes[0][3] == {"usage": 0.97}

    def test_metadata_passthrough_on_convenience_methods(self) -> None:
        svc = NotificationService()
        event = svc.warning("Warn", "detail", metadata={"attempts": 3})
        assert event.metadata["attempts"] == 3

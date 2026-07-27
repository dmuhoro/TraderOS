from __future__ import annotations

from traderos.domain.services.notification_service import NotificationChannel
from traderos.domain.services.notification_service import NotificationLevel
from traderos.domain.services.notification_service import NotificationService


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
        event = svc.info("Multi")
        assert event.channel in {NotificationChannel.CONSOLE, NotificationChannel.FILE}

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from enum import Enum
from typing import NamedTuple

from traderos.domain.ports import NotifierPort


class NotificationChannel(Enum):
    CONSOLE = "console"
    FILE = "file"
    WEBHOOK = "webhook"


class NotificationLevel(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class NotificationEvent(NamedTuple):
    channel: NotificationChannel
    level: NotificationLevel
    title: str
    message: str
    timestamp: datetime
    metadata: dict[str, str | float | int | None]


@dataclass
class NotificationService:
    channels: set[NotificationChannel] = field(
        default_factory=lambda: {NotificationChannel.CONSOLE}
    )
    log: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))
    notifier: NotifierPort | None = None

    def send(
        self,
        level: NotificationLevel,
        title: str,
        message: str,
        channel: NotificationChannel | None = None,
        metadata: dict[str, str | float | int | None] | None = None,
    ) -> NotificationEvent:
        ch = channel or next(iter(self.channels))
        event = NotificationEvent(
            channel=ch,
            level=level,
            title=title,
            message=message,
            timestamp=datetime.now(UTC),
            metadata=metadata or {},
        )
        if ch == NotificationChannel.CONSOLE:
            self._send_console(event)
        elif ch == NotificationChannel.FILE:
            self._send_file(event)
        elif ch == NotificationChannel.WEBHOOK:
            self._send_webhook(event)
        return event

    def info(
        self,
        title: str,
        message: str = "",
        channel: NotificationChannel | None = None,
    ) -> NotificationEvent:
        return self.send(NotificationLevel.INFO, title, message, channel)

    def warning(
        self,
        title: str,
        message: str = "",
        channel: NotificationChannel | None = None,
    ) -> NotificationEvent:
        return self.send(NotificationLevel.WARNING, title, message, channel)

    def error(
        self,
        title: str,
        message: str = "",
        channel: NotificationChannel | None = None,
    ) -> NotificationEvent:
        return self.send(NotificationLevel.ERROR, title, message, channel)

    def critical(
        self,
        title: str,
        message: str = "",
        channel: NotificationChannel | None = None,
    ) -> NotificationEvent:
        return self.send(NotificationLevel.CRITICAL, title, message, channel)

    def _send_console(self, event: NotificationEvent) -> None:
        msg = f"[{event.level.name}] {event.title}"
        if event.message:
            msg += f": {event.message}"
        if event.level == NotificationLevel.ERROR:
            self.log.error(msg)
        elif event.level == NotificationLevel.WARNING:
            self.log.warning(msg)
        elif event.level == NotificationLevel.CRITICAL:
            self.log.critical(msg)
        else:
            self.log.info(msg)

    def _send_file(self, event: NotificationEvent) -> None:
        line = json.dumps(
            {
                "level": event.level.name,
                "title": event.title,
                "message": event.message,
                "timestamp": event.timestamp.isoformat(),
                "metadata": event.metadata,
            }
        )
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "notifications.jsonl")
        with open(log_path, "a") as f:
            f.write(line + "\n")

    def _send_webhook(self, event: NotificationEvent) -> None:
        if self.notifier is None:
            self.log.info("No webhook notifier configured; skipping webhook delivery")
            return
        self.notifier.send_notification(
            title=event.title,
            message=event.message,
            level=event.level.name,
            metadata=event.metadata,
        )

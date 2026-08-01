from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any

LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(10 * 1024 * 1024)))
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))


def _file_handler(path: str) -> logging.Handler:
    return RotatingFileHandler(
        path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
    )


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            data["exception"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra", None)
        if extra:
            data["data"] = {k: str(v) for k, v in extra.items()}
        return json.dumps(data)


def setup_json_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_file = os.getenv("LOG_FILE")
    handler: logging.Handler
    if log_file:
        handler = _file_handler(log_file)
    else:
        handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))


class StructuredLogger:
    def __init__(self, name: str, level: str = "INFO", log_file: str | None = None) -> None:
        self._logger = logging.getLogger(name)
        self._logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        self._logger.handlers.clear()

        formatter = logging.Formatter("%(message)s")

        handler: logging.Handler
        if log_file:
            handler = _file_handler(log_file)
        else:
            handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        self._logger.addHandler(handler)

    def _log(self, level: int, event: str, **kwargs: Any) -> None:
        record: dict[str, Any] = {
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "event": event,
            "logger": self._logger.name,
        }
        if kwargs:
            record["data"] = {k: str(v) for k, v in kwargs.items()}
        self._logger.log(level, json.dumps(record))

    def info(self, event: str, **kwargs: Any) -> None:
        self._log(logging.INFO, event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._log(logging.WARNING, event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, event, **kwargs)

    def debug(self, event: str, **kwargs: Any) -> None:
        self._log(logging.DEBUG, event, **kwargs)

    def critical(self, event: str, **kwargs: Any) -> None:
        self._log(logging.CRITICAL, event, **kwargs)

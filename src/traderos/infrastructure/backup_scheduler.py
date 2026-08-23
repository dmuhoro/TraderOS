"""Periodic automatic database backup scheduler.

Sprint 46 made ``pg_dump`` available in the production image (and proved the
backup→restore round-trip against the live Postgres), but nothing scheduled
backups — a launch-ready deployment must back up its database automatically,
not on operator recall. ``BackupScheduler`` mirrors the ``ProbeScheduler``
thread-loop pattern: run ``create_backup()`` every ``interval_seconds``,
surfacing every failure (callback + metric), never silent.

Fail-closed by design: a backup that raises is recorded on the instance
(``last_error``) and delivered to the on-call/notification seam if provided —
a silently-skipped backup is exactly the kind of quiet failure that turns an
incident into a data-loss event.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BACKUP_INTERVAL_SECONDS = 3600  # hourly by default

# Env knobs (documented in .env.example / config): DB_BACKUP_INTERVAL_SECONDS
# to change the cadence, DB_BACKUP_DIR + DB_MAX_BACKUPS already rotate the
# backup set (see traderos.infrastructure.database.backup).
BACKUP_INTERVAL_VAR = "DB_BACKUP_INTERVAL_SECONDS"


def _interval_from_env() -> int:
    raw = __import__("os").getenv(BACKUP_INTERVAL_VAR, "")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_BACKUP_INTERVAL_SECONDS


class BackupScheduler:
    """Runs the real backup path on a timer with explicit failure surfacing.

    ``create_backup`` is resolved lazily inside ``_run_once`` so tests can
    inject a fake; ``on_failure`` (optional) receives the exception so the
    wiring can alert/pager; ``metrics`` (optional) bumps a counter on success
    and failure.
    """

    def __init__(
        self,
        interval_seconds: int | None = None,
        on_failure: Callable[[Exception], None] | None = None,
        metrics: Any | None = None,
        create_backup: Callable[[Any | None], Path] | None = None,
    ) -> None:
        self._interval = interval_seconds if interval_seconds is not None else _interval_from_env()
        self._on_failure = on_failure
        self._metrics = metrics
        self._create_backup = create_backup
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_success: datetime | None = None
        self._last_error: str | None = None
        self._backup_count = 0

    @property
    def interval_seconds(self) -> int:
        return self._interval

    @property
    def last_success(self) -> datetime | None:
        with self._lock:
            return self._last_success

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    @property
    def backup_count(self) -> int:
        with self._lock:
            return self._backup_count

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="backup-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        # Wait for the interval first (like ProbeScheduler) so short-lived
        # processes never fire a backup they did not intend to.
        while not self._stop_event.is_set():
            if self._stop_event.wait(self._interval):
                break
            # _run_once never raises: it records every failure on the instance
            # and delivers it to on_failure. One bad tick must not kill the loop.
            self._run_once()

    def _run_once(self) -> None:
        create = self._create_backup or self._default_create_backup
        try:
            path = create(None)
        except Exception as exc:  # noqa: BLE001 — failures are surfaced, never silent
            self._record_failure(exc)
            return
        with self._lock:
            self._last_success = datetime.now(UTC)
            self._backup_count += 1
            self._last_error = None
        logger.info("automatic backup created: %s", path)
        if self._metrics is not None:
            try:
                self._metrics.counter("backup.scheduled.created", 1.0)
            except Exception:  # noqa: BLE001, S110 — metrics must never break backups
                pass

    @staticmethod
    def _default_create_backup(_config: Any | None) -> Path:
        from traderos.infrastructure.database.backup import create_backup

        return create_backup()

    def _record_failure(self, exc: Exception) -> None:
        with self._lock:
            self._last_error = str(exc)
        logger.error("automatic backup FAILED: %s", exc)
        if self._metrics is not None:
            try:
                self._metrics.counter("backup.scheduled.failed", 1.0)
            except Exception:  # noqa: BLE001, S110 — metrics must never break backups
                pass
        if self._on_failure is not None:
            self._on_failure(exc)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "interval_seconds": self._interval,
                "backup_count": self._backup_count,
                "last_success": self._last_success.isoformat() if self._last_success else None,
                "last_error": self._last_error,
                "running": self._thread is not None and self._thread.is_alive(),
            }

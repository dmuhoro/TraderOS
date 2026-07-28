from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from typing import Any

_LOGGER = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = float(os.getenv("LEADER_HEARTBEAT_INTERVAL", "5.0"))
LEASE_DURATION = float(os.getenv("LEADER_LEASE_DURATION", "30.0"))


class LeaderElection:
    def __init__(self, conn: Any, lock_id: int = 20240728) -> None:
        self._conn = conn
        self._lock_id = lock_id
        self._is_leader = False
        self._stop_event = threading.Event()
        self._bg_thread: threading.Thread | None = None
        self._on_elected: Callable[[], None] | None = None
        self._on_deposed: Callable[[], None] | None = None

    @property
    def is_leader(self) -> bool:
        return self._is_leader

    def on_elected(self, callback: Callable[[], None]) -> None:
        self._on_elected = callback

    def on_deposed(self, callback: Callable[[], None]) -> None:
        self._on_deposed = callback

    def try_acquire(self) -> bool:
        try:
            cur = self._conn.execute("SELECT pg_try_advisory_lock(%s)", (self._lock_id,))
            acquired = cur.fetchone()[0]
            if acquired and not self._is_leader:
                self._is_leader = True
                _LOGGER.info("LeaderElection: acquired leadership")
                if self._on_elected:
                    self._on_elected()
            elif not acquired and self._is_leader:
                self._is_leader = False
                _LOGGER.warning("LeaderElection: lost leadership")
                if self._on_deposed:
                    self._on_deposed()
            return self._is_leader
        except Exception:
            if self._is_leader:
                self._is_leader = False
                _LOGGER.error("LeaderElection: connection error, lost leadership")
                if self._on_deposed:
                    self._on_deposed()
            return False

    def release(self) -> None:
        try:
            self._conn.execute("SELECT pg_advisory_unlock(%s)", (self._lock_id,))
        except Exception:
            pass
        self._is_leader = False

    def start(self) -> None:
        self._stop_event.clear()
        self._bg_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name="leader-election"
        )
        self._bg_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._bg_thread and self._bg_thread.is_alive():
            self._bg_thread.join(timeout=5)
        self.release()

    def _heartbeat_loop(self) -> None:
        while not self._stop_event.is_set():
            self.try_acquire()
            self._stop_event.wait(HEARTBEAT_INTERVAL)


class FileBasedLeaderElection:
    def __init__(self, lock_path: str = "/tmp/traderos_leader.lock") -> None:
        self._lock_path = lock_path
        self._lock_file: Any = None
        self._is_leader = False
        self._on_elected: Callable[[], None] | None = None
        self._on_deposed: Callable[[], None] | None = None

    @property
    def is_leader(self) -> bool:
        return self._is_leader

    def on_elected(self, callback: Callable[[], None]) -> None:
        self._on_elected = callback

    def on_deposed(self, callback: Callable[[], None]) -> None:
        self._on_deposed = callback

    def try_acquire(self) -> bool:
        try:
            self._lock_file = open(self._lock_path, "w")
            import fcntl

            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not self._is_leader:
                self._is_leader = True
                _LOGGER.info("FileLeaderElection: acquired leadership")
                if self._on_elected:
                    self._on_elected()
            return True
        except (OSError, ImportError):
            if self._is_leader:
                self._is_leader = False
                _LOGGER.warning("FileLeaderElection: lost leadership")
                if self._on_deposed:
                    self._on_deposed()
            if self._lock_file:
                self._lock_file.close()
                self._lock_file = None
            return False

    def release(self) -> None:
        if self._lock_file:
            try:
                import fcntl

                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            self._lock_file.close()
            self._lock_file = None
        self._is_leader = False

    def start(self) -> None:
        self.try_acquire()

    def stop(self) -> None:
        self.release()
        try:
            os.unlink(self._lock_path)
        except OSError:
            pass

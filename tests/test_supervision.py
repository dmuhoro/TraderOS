from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import UTC
from datetime import datetime
from datetime import timedelta

from traderos.domain.services.notification_service import NotificationChannel
from traderos.domain.services.notification_service import NotificationService
from traderos.infrastructure.supervision import JsonlHeartbeatStore
from traderos.infrastructure.supervision import SupervisionService

_CHILD = """
import time, sys
sys.path.insert(0, {cwd!r})
from traderos.infrastructure.supervision import HeartbeatRecord
from traderos.infrastructure.supervision import JsonlHeartbeatStore
from datetime import datetime, timezone
store = JsonlHeartbeatStore({path!r})
store.append(HeartbeatRecord(ts=datetime.now(timezone.utc), pid=__import__('os').getpid(),
    action='heartbeat', component='daemon'))
time.sleep(30)
"""


class _RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send_notification(self, title, message, level, metadata=None):
        self.calls.append(
            {"title": title, "message": message, "level": level, "metadata": metadata or {}}
        )


def _supervisor(store, rec, stale_after=0.0):
    notifications = NotificationService(channels={NotificationChannel.WEBHOOK}, notifier=rec)
    sup = SupervisionService(
        store=store,
        notifications=notifications,
        stale_after_seconds=stale_after,
    )
    sup._now = lambda: datetime.now(UTC) + timedelta(seconds=60)
    return sup


class TestSupervision:
    def test_force_killed_process_delivers_critical_alert(self, tmp_path) -> None:
        store_path = tmp_path / "supervision.jsonl"
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _CHILD.format(cwd=os.getcwd(), path=str(store_path)),
            ],
            env={**os.environ, "PYTHONPATH": os.getcwd()},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1.5)
        assert child.poll() is None, "child died before the kill"
        child.kill()
        child.wait()

        rec = _RecordingNotifier()
        store = JsonlHeartbeatStore(store_path)
        assert store.last().action == "heartbeat"
        sup = _supervisor(store, rec)
        assert sup.check_unclean_shutdown() is True
        assert rec.calls, "a CRITICAL alert must be delivered after a forced kill"
        assert rec.calls[0]["level"] == "CRITICAL"
        assert "Unclean Process Death" in rec.calls[0]["title"]
        assert "was likely killed" in rec.calls[0]["message"]

    def test_clean_shutdown_does_not_alert(self, tmp_path) -> None:
        store = JsonlHeartbeatStore(tmp_path / "supervision.jsonl")
        rec = _RecordingNotifier()
        sup = _supervisor(store, rec)
        sup.heartbeat()
        sup.mark_clean_shutdown()
        assert sup.check_unclean_shutdown() is False
        assert rec.calls == []

    def test_fresh_heartbeat_does_not_alert(self, tmp_path) -> None:
        store = JsonlHeartbeatStore(tmp_path / "supervision.jsonl")
        rec = _RecordingNotifier()
        notifications = NotificationService(channels={NotificationChannel.WEBHOOK}, notifier=rec)
        sup = SupervisionService(store=store, notifications=notifications)
        sup.heartbeat()
        assert sup.check_unclean_shutdown() is False
        assert rec.calls == []

    def test_no_history_does_not_alert(self, tmp_path) -> None:
        store = JsonlHeartbeatStore(tmp_path / "supervision.jsonl")
        rec = _RecordingNotifier()
        assert _supervisor(store, rec).check_unclean_shutdown() is False
        assert rec.calls == []

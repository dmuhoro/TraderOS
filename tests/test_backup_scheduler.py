from __future__ import annotations

import time
from pathlib import Path

import pytest

from traderos.infrastructure.backup_scheduler import DEFAULT_BACKUP_INTERVAL_SECONDS
from traderos.infrastructure.backup_scheduler import BackupScheduler


class TestBackupScheduler:
    def test_default_interval_from_env_fallback(self) -> None:
        # No DB_BACKUP_INTERVAL_SECONDS set -> the documented default.
        sched = BackupScheduler()
        assert sched.interval_seconds == DEFAULT_BACKUP_INTERVAL_SECONDS

    def test_interval_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_BACKUP_INTERVAL_SECONDS", "120")
        assert BackupScheduler().interval_seconds == 120

    def test_run_once_creates_backup_and_records_success(self) -> None:
        created: list[Path] = []

        def _fake_create(_config):
            created.append(Path("/tmp/fake.dump"))
            return created[-1]

        sched = BackupScheduler(create_backup=_fake_create)
        sched._run_once()
        assert len(created) == 1
        assert sched.backup_count == 1
        assert sched.last_success is not None
        assert sched.last_error is None

    def test_failure_is_recorded_and_delivered_not_silent(self) -> None:
        failures: list[Exception] = []

        def _explode(_config):
            raise RuntimeError("disk full")

        sched = BackupScheduler(create_backup=_explode, on_failure=failures.append)
        sched._run_once()
        assert len(failures) == 1
        assert "disk full" in (sched.last_error or "")
        assert sched.backup_count == 0
        assert sched.last_success is None

    def test_failure_does_not_kill_the_loop(self) -> None:
        calls = {"n": 0}

        def _flaky(_config):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("transient")
            return Path("/tmp/ok.dump")

        sched = BackupScheduler(create_backup=_flaky)
        sched._run_once()  # fails
        sched._run_once()  # succeeds
        assert calls["n"] == 2
        assert sched.backup_count == 1
        assert sched.last_success is not None
        assert sched.last_error is None  # cleared on the success

    def test_metrics_counted_on_success_and_failure(self) -> None:
        counters: dict[str, float] = {}

        class _Metrics:
            def counter(self, name: str, value: float) -> None:
                counters[name] = value

        ok_sched = BackupScheduler(create_backup=lambda _c: Path("/tmp/a.dump"), metrics=_Metrics())
        ok_sched._run_once()
        assert counters.get("backup.scheduled.created") == 1.0

        fail_sched = BackupScheduler(
            create_backup=lambda _c: (_ for _ in ()).throw(RuntimeError("boom")),
            metrics=_Metrics(),
        )
        fail_sched._run_once()
        assert counters.get("backup.scheduled.failed") == 1.0

    def test_metrics_raise_never_breaks_backup(self) -> None:
        class _ExplodingMetrics:
            def counter(self, name: str, value: float) -> None:
                raise RuntimeError("metrics down")

        created = []

        def _fake(_c):
            created.append(1)
            return Path("/tmp/a.dump")

        ok_sched = BackupScheduler(create_backup=_fake, metrics=_ExplodingMetrics())
        ok_sched._run_once()  # must not raise despite metrics exploding
        assert len(created) == 1
        assert ok_sched.backup_count == 1

        fail_sched = BackupScheduler(
            create_backup=lambda _c: (_ for _ in ()).throw(RuntimeError("boom")),
            metrics=_ExplodingMetrics(),
        )
        fail_sched._run_once()  # failure recorded, metrics exploding is swallowed
        assert fail_sched.backup_count == 0
        assert "boom" in (fail_sched.last_error or "")

    def test_default_create_backup_runs_the_real_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = []

        def _fake_create_backup(_config=None):
            calls.append(1)
            return Path("/tmp/real.dump")

        monkeypatch.setattr(
            "traderos.infrastructure.database.backup.create_backup",
            _fake_create_backup,
        )
        result = BackupScheduler._default_create_backup(None)
        assert result == Path("/tmp/real.dump")
        assert len(calls) == 1

    def test_on_failure_receives_the_exception(self) -> None:
        received: list[Exception] = []

        def _explode(_c):
            raise OSError("disk full")

        sched = BackupScheduler(create_backup=_explode, on_failure=received.append)
        sched._run_once()
        assert len(received) == 1
        assert isinstance(received[0], OSError)

    def test_start_stop_loop_runs_backup_then_stops(self) -> None:
        created = []

        def _fake_create(_config):
            created.append(Path("/tmp/fake.dump"))
            return created[-1]

        sched = BackupScheduler(interval_seconds=0.01, create_backup=_fake_create)
        sched.start()
        deadline = time.time() + 2.0
        while len(created) < 1 and time.time() < deadline:
            time.sleep(0.01)
        sched.stop()
        assert len(created) >= 1
        assert sched.backup_count >= 1
        assert sched.stats()["running"] is False

    def test_start_is_idempotent(self) -> None:
        sched = BackupScheduler(interval_seconds=3600)
        sched.start()
        thread = sched._thread
        sched.start()  # no-op while alive
        assert sched._thread is thread
        sched.stop()

    def test_stats_shape(self) -> None:
        sched = BackupScheduler()
        stats = sched.stats()
        assert {"interval_seconds", "backup_count", "last_success", "last_error", "running"} <= set(
            stats
        )


class TestBackupSchedulerWiring:
    def test_orchestrator_exposes_backup_scheduler(self) -> None:
        from traderos.application.factory import build_orchestrator

        orch = build_orchestrator(mode="paper")
        assert orch.backup_scheduler is not None
        stats = orch.backup_scheduler.stats()
        assert stats["running"] is False  # not started by construction

    def test_orchestrator_start_stop_controls_scheduler(self) -> None:
        from traderos.application.factory import build_orchestrator

        orch = build_orchestrator(mode="paper")
        # Only probe/backup schedulers, not the daemon itself.
        if orch.backup_scheduler is not None:
            orch.backup_scheduler.start()
            assert orch.backup_scheduler.stats()["running"] is True
            orch.backup_scheduler.stop()
            assert orch.backup_scheduler.stats()["running"] is False

    def test_status_includes_backups(self) -> None:
        from traderos.application.factory import build_orchestrator

        orch = build_orchestrator(mode="paper")
        status = orch.get_status()
        assert "backups" in status
        assert status["backups"]["interval_seconds"] >= 0

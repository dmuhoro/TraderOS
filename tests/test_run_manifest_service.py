from __future__ import annotations

import sqlite3

from traderos.infrastructure.run_manifest import DurableRunManifest
from traderos.infrastructure.run_manifest import RunManifestService


class TestRunManifestService:
    def test_record(self) -> None:
        svc = RunManifestService()
        entry = svc.record("backtest", "run", "completed", 150.0)
        assert entry.service == "backtest"
        assert entry.action == "run"
        assert entry.status == "completed"
        assert entry.duration_ms == 150.0

    def test_get_runs_all(self) -> None:
        svc = RunManifestService()
        svc.record("backtest", "run")
        svc.record("signal", "evaluate")
        assert len(svc.get_runs()) == 2

    def test_get_runs_filtered(self) -> None:
        svc = RunManifestService()
        svc.record("backtest", "run")
        svc.record("signal", "evaluate")
        results = svc.get_runs(service="backtest")
        assert len(results) == 1

    def test_summary(self) -> None:
        svc = RunManifestService()
        svc.record("backtest", "run")
        svc.record("backtest", "optimize")
        svc.record("signal", "evaluate")
        summary = svc.summary()
        assert summary["backtest"] == 2
        assert summary["signal"] == 1

    def test_clear(self) -> None:
        svc = RunManifestService()
        svc.record("test", "run")
        svc.clear()
        assert svc.get_runs() == []

    def test_record_with_metadata(self) -> None:
        svc = RunManifestService()
        entry = svc.record("backtest", "run", metadata={"candles": 500, "strategy": "ma"})
        assert entry.metadata["candles"] == 500
        assert entry.metadata["strategy"] == "ma"

    def test_get_runs_limit(self) -> None:
        svc = RunManifestService()
        for i in range(10):
            svc.record("test", f"run_{i}")
        assert len(svc.get_runs(limit=3)) == 3

    def test_get_runs_offset(self) -> None:
        svc = RunManifestService()
        for i in range(10):
            svc.record("test", f"run_{i}")
        assert len(svc.get_runs(limit=3, offset=2)) == 3
        assert len(svc.get_runs(limit=100, offset=8)) == 2


class TestDurableRunManifest:
    """SQLite-backed manifest: crash detection, filtering, clear, close."""

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        return conn

    def test_detect_unclean_shutdown_true_without_stop(self) -> None:
        conn = self._conn()
        manifest = DurableRunManifest(conn=conn)
        manifest.record("daemon", "start", status="running")
        assert manifest.detect_unclean_shutdown("daemon") is True

    def test_detect_unclean_shutdown_false_after_stop(self) -> None:
        conn = self._conn()
        manifest = DurableRunManifest(conn=conn)
        manifest.record("daemon", "start", status="running")
        manifest.record("daemon", "stop")
        assert manifest.detect_unclean_shutdown("daemon") is False

    def test_empty_manifest_is_clean(self) -> None:
        manifest = DurableRunManifest(conn=self._conn())
        assert manifest.detect_unclean_shutdown("daemon") is False

    def test_get_runs_without_service_returns_everything(self) -> None:
        conn = self._conn()
        manifest = DurableRunManifest(conn=conn)
        manifest.record("backtest", "run")
        manifest.record("signal", "evaluate")
        assert len(manifest.get_runs()) == 2
        assert len(manifest.get_runs(service="backtest")) == 1

    def test_get_runs_offset(self) -> None:
        conn = self._conn()
        manifest = DurableRunManifest(conn=conn)
        for i in range(6):
            manifest.record("test", f"run_{i}")
        assert len(manifest.get_runs(limit=2, offset=2)) == 2
        assert len(manifest.get_runs(limit=2, offset=5)) == 1

    def test_clear_empties_the_table(self) -> None:
        conn = self._conn()
        manifest = DurableRunManifest(conn=conn)
        manifest.record("backtest", "run")
        manifest.clear()
        assert manifest.get_runs() == []
        assert manifest.summary() == {}

    def test_summary_counts_per_service(self) -> None:
        conn = self._conn()
        manifest = DurableRunManifest(conn=conn)
        manifest.record("backtest", "run")
        manifest.record("backtest", "optimize")
        assert manifest.summary() == {"backtest": 2}

    def test_close_is_idempotent(self) -> None:
        conn = self._conn()
        manifest = DurableRunManifest(conn=conn)
        manifest.record("backtest", "run")
        manifest.close()
        manifest.close()  # closing an already-closed connection must not raise

    def test_close_survives_connection_failure(self) -> None:
        class _BoomConn:
            def execute(self, sql, *args):
                return self

            def commit(self) -> None:
                return None

            def fetchall(self):
                return []

            def close(self) -> None:
                raise RuntimeError("transport died")

        manifest = DurableRunManifest(conn=_BoomConn())
        manifest.close()  # best-effort teardown: never propagates

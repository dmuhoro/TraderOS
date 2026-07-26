from __future__ import annotations

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

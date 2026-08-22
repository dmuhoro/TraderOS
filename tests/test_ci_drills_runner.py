from __future__ import annotations

import sys
from pathlib import Path

import pytest

import scripts.evidence.run_ci_drills as runner


def _write_fake_script(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


class TestDrillInventory:
    def test_every_ci_drill_script_exists_on_disk(self) -> None:
        for name, script in runner.DRILLS:
            assert (runner.SCRIPTS_DIR / script).is_file(), f"{name}: {script} missing"

    def test_inventory_has_expected_credential_free_set(self) -> None:
        assert {name for name, _ in runner.DRILLS} == {
            "account",
            "auth_fail_closed",
            "causal_replay",
            "firm_ops",
            "governance",
            "market_brain",
            "multirestart_replay",
            "oncall_transport",
            "operational_health",
            "oracle_conformance",
            "paper_soak",
            "partial_fill_reconnect",
            "real_market_walk_forward",
            "rate_limiter_burst",
            "risk_rails",
            "runbook_cli",
            "secret_lifecycle",
            "trigger_alerting",
            "walk_forward_evidence",
        }

    def test_key_gated_scripts_are_documented_and_excluded(self) -> None:
        """No credential/network-gated drill may silently join the CI set."""
        scripts_in_ci = {script for _, script in runner.DRILLS}
        for script in runner.KEY_GATED:
            assert (runner.SCRIPTS_DIR / script).is_file(), f"{script} missing on disk"
            assert script not in scripts_in_ci, f"{script} must not run in CI"


class TestAggregation:
    def test_aggregate_passes_when_every_drill_passed(self) -> None:
        results = [
            runner.DrillResult("a", True, "ok"),
            runner.DrillResult("b", True, "ok"),
        ]
        assert runner.aggregate(results) == 0

    def test_aggregate_fails_when_any_drill_failed(self) -> None:
        results = [
            runner.DrillResult("a", True, "ok"),
            runner.DrillResult("b", False, "boom"),
        ]
        assert runner.aggregate(results) == 1

    def test_aggregate_fails_on_empty_suite(self) -> None:
        """An empty run is a FAIL, never a silent all-green."""
        assert runner.aggregate([]) == 1


class TestRunDrill:
    def test_run_drill_passes_on_exit_zero(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _write_fake_script(tmp_path / "run_paper_soak.py", "import sys\nsys.exit(0)\n")
        monkeypatch.setattr(runner, "SCRIPTS_DIR", tmp_path)
        result = runner.run_drill("paper_soak")
        assert result.ok is True
        assert result.name == "paper_soak"

    def test_run_drill_fails_on_nonzero_exit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _write_fake_script(tmp_path / "run_paper_soak.py", "import sys\nsys.exit(7)\n")
        monkeypatch.setattr(runner, "SCRIPTS_DIR", tmp_path)
        result = runner.run_drill("paper_soak")
        assert result.ok is False
        assert "7" in result.detail or "exit" in result.detail

    def test_run_drill_fails_on_crash(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _write_fake_script(tmp_path / "run_paper_soak.py", "raise RuntimeError('exploded')\n")
        monkeypatch.setattr(runner, "SCRIPTS_DIR", tmp_path)
        result = runner.run_drill("paper_soak")
        assert result.ok is False
        assert "exploded" in result.detail

    def test_run_drill_times_out(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _write_fake_script(tmp_path / "run_paper_soak.py", "import time\ntime.sleep(30)\n")
        monkeypatch.setattr(runner, "SCRIPTS_DIR", tmp_path)
        result = runner.run_drill("paper_soak", timeout=0.5)
        assert result.ok is False
        assert "TIMEOUT" in result.detail

    def test_run_drill_uses_sys_executable_by_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _write_fake_script(
            tmp_path / "run_paper_soak.py",
            f"import sys\nsys.exit(0 if sys.executable == {sys.executable!r} else 9)\n",
        )
        monkeypatch.setattr(runner, "SCRIPTS_DIR", tmp_path)
        assert runner.run_drill("paper_soak").ok is True


class TestEvidenceLog:
    def test_write_evidence_log_records_verdict(self, tmp_path: Path) -> None:
        out = tmp_path / "evidence.log"
        results = [
            runner.DrillResult("a", True, "ok"),
            runner.DrillResult("b", False, "boom"),
        ]
        runner.write_evidence_log(results, out=out)
        text = out.read_text()
        assert "[PASS] a: ok" in text
        assert "[FAIL] b: boom" in text
        assert "VERDICT: FAIL — 1/2" in text

    def test_main_lists_inventory(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert runner.main(["--list"]) == 0
        out = capsys.readouterr().out
        assert "risk_rails: run_risk_rails_drill.py" in out
        assert "19 credential-free drills" in out

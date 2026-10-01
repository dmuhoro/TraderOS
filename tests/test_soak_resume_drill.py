"""G-02 unattended soak: durable checkpoint + resume across a supervisor restart.

This is the defect that ended the 72h Railway soak: the runner measured its
wall-clock window from ``time.monotonic()`` at *process* start, so every
restart the supervisor performed on failure began a fresh 72h clock. The soak
could never finish — the service exhausted ``ON_FAILURE`` retries and went
CRASHED with no verdict, which is indistinguishable from a run that produced a
verdict.

These tests drive the REAL runner (``run_unattended_paper_soak.py``) as a
subprocess and kill it, because the property under test *is* what survives a
process death. They are timing-based but bounded and generous: each asserts an
ordering/continuity invariant with slack, never an exact wall-clock value.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "scripts" / "evidence" / "run_unattended_paper_soak.py"
EVIDENCE = REPO_ROOT / "docs" / "evidence"
LABEL = "soak_resume_drill"

# A ~30s window keeps the drill honest (it really does span a kill/restart)
# while staying inside a unit-test budget. The property under test — elapsed
# time ACCUMULATES across a restart instead of resetting — is scale
# independent, so a short window proves exactly what a 72h window does.
WINDOW_SECONDS = 30.0
BATCH_SECONDS = 0.5


@pytest.fixture
def runner(tmp_path: Path):
    """The real runner, pointed at a stub harness, writing to a temp volume.

    Only the HARNESS is stubbed (and REPO_ROOT, so evidence lands in tmp_path).
    The checkpoint/resume logic under test is the runner's own, unmodified.
    """
    harness = tmp_path / "stub_harness.py"
    harness.write_text(
        "#!/usr/bin/env python3\n"
        "import os, time\n"
        f"time.sleep({BATCH_SECONDS})\n"
        "print('cycles=1 filled=1 VERDICT: PASS')\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)

    src = RUNNER.read_text(encoding="utf-8")
    src = src.replace(
        'HARNESS = REPO_ROOT / "scripts" / "evidence" / "run_real_paper_soak.py"',
        f'HARNESS = Path("{harness}")',
    ).replace(
        "REPO_ROOT = Path(__file__).resolve().parents[2]",
        f'REPO_ROOT = Path("{tmp_path}")',
    )
    shim = tmp_path / "runner.py"
    shim.write_text(src, encoding="utf-8")

    (tmp_path / "docs" / "evidence").mkdir(parents=True, exist_ok=True)

    env = dict(
        os.environ,
        PYTHONPATH=".",
        ALPACA_API_KEY="stub-key",
        ALPACA_SECRET_KEY="stub-secret",
        SOAK_LOG_LABEL=LABEL,
    )

    def _run(*extra: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(shim),
                "--seconds",
                str(WINDOW_SECONDS),
                "--batch-cycles",
                "1",
                "--interval-minutes",
                "0",
                *extra,
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def _state() -> dict:
        return json.loads(
            (tmp_path / "docs" / "evidence" / f"{LABEL}_soak_state.json").read_text(
                encoding="utf-8"
            )
        )

    return _run, _state, tmp_path


def test_window_survives_a_kill_and_resumes(runner) -> None:
    """The headline defect: kill mid-window, restart, the clock does not reset.

    Asserts the *observable* consequence, not an implementation detail: the
    restarted run reports RESUME with a non-zero elapsed time and carries on to
    the full window. Pre-fix, run 2 reported a fresh window and would have run
    another full window from zero — and on Railway, another 72h.
    """
    run, state, tmp = runner
    interval_minutes = str(BATCH_SECONDS / 60.0)

    proc = subprocess.Popen(
        [
            sys.executable,
            str(tmp / "runner.py"),
            "--seconds",
            str(WINDOW_SECONDS),
            "--batch-cycles",
            "1",
            "--interval-minutes",
            interval_minutes,
        ],
        cwd=REPO_ROOT,
        env=dict(
            os.environ,
            PYTHONPATH=".",
            ALPACA_API_KEY="stub-key",
            ALPACA_SECRET_KEY="stub-secret",
            SOAK_LOG_LABEL=LABEL,
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    # Long enough to complete real batches, short enough not to finish.
    time.sleep(BATCH_SECONDS * 3)
    proc.kill()
    proc.wait(timeout=30)

    first = state()
    assert first["elapsed_seconds"] > 0, "a killed run must have burned elapsed time"
    assert first["batch"] > 0, "a killed run must have completed at least one batch"
    assert first["completed"] is False, "a killed run must not claim completion"

    second = run("--interval-minutes", interval_minutes)
    assert second.returncode == 0, second.stdout[-2000:]
    assert "RESUME" in second.stdout, f"restart must resume, not reset:\n{second.stdout[-2000:]}"

    final = state()
    assert final["elapsed_seconds"] >= first["elapsed_seconds"], "elapsed went backwards"
    assert final["elapsed_seconds"] >= WINDOW_SECONDS * 0.9, (
        f"resumed window must total the full target, got {final['elapsed_seconds']:.0f}s "
        f"of {WINDOW_SECONDS:.0f}s"
    )
    assert final["batch"] > first["batch"], "resume must continue, not rewind the batch counter"
    assert final["restarts"] == 1
    assert final["completed"] is True
    assert final["verdict"] == "PASS"
    assert first["started_at"] == final["started_at"], (
        "the soak's own start time must be preserved across the restart — "
        "this is the 72h clock that must not reset"
    )


def test_completed_window_is_not_rerun(runner) -> None:
    """A decided verdict must not be overwritten by a second soak.

    Re-running a completed window would replace a real 72h result with an
    unrelated new one — a silently falsified evidence record.
    """
    run, state, _tmp = runner
    assert run().returncode == 0
    decided = state()
    assert decided["completed"] is True

    again = run()
    assert "already completed" in again.stdout
    assert state()["batch"] == decided["batch"], "a completed window must add no batches"


def test_mismatched_geometry_is_superseded_not_spliced(runner) -> None:
    """A checkpoint from a different soak shape is never resumed into this one.

    Resuming across a changed window/batch-size would splice two different
    experiments into one PASS/FAIL verdict. The old checkpoint is preserved as
    ``.superseded`` (auditable) and a fresh window begins.
    """
    run, state, tmp = runner
    assert run().returncode == 0
    original = state()

    # `--seconds 0` cancels the fixture's default window: the window flags are
    # additive, so leaving `--seconds 30` in place would make this run's window
    # 33s rather than the 3s the geometry mismatch is meant to demonstrate.
    mismatched = run("--seconds", "0", "--minutes", "0.05", "--batch-cycles", "5")
    assert mismatched.returncode in (0, 1)
    assert "geometry differs" in mismatched.stdout, mismatched.stdout[-2000:]

    superseded = tmp / "docs" / "evidence" / f"{LABEL}_soak_state.json.superseded"
    assert superseded.exists(), "the old checkpoint must be preserved, not deleted"
    assert json.loads(superseded.read_text(encoding="utf-8"))["elapsed_seconds"] == pytest.approx(
        original["elapsed_seconds"]
    )
    assert (
        state()["elapsed_seconds"] < original["elapsed_seconds"]
    ), "a fresh window must start from zero, not inherit the old elapsed time"


def test_changed_harness_supersedes_the_checkpoint_instead_of_splicing_it(runner) -> None:
    """A checkpoint is not resumable across a change of HARNESS code.

    The geometry guard could not see the case that mattered in production: the
    fixed soak was redeployed on top of a checkpoint whose batches had been
    produced by the leaky one. The window would have inherited those FAILED
    batches and could never return PASS, leaving the operator to either accept a
    permanently-red window or hand-edit the checkpoint by hand. Neither is
    honest, so a different harness digest supersedes the checkpoint exactly as a
    different geometry does.
    """
    run, state, tmp = runner
    assert run().returncode == 0
    original = state()

    fp_path = tmp / "docs" / "evidence" / f"{LABEL}_soak_state.json"
    state_data = json.loads(fp_path.read_text(encoding="utf-8"))
    digest = state_data["harness_digest"]
    assert digest, "the checkpoint must record which harness produced it"

    # Simulate the operator deploying a different harness revision.
    state_data["harness_digest"] = "0000000000000000"
    state_data["batches_failed"] = 2  # the failed batches that must NOT be inherited
    state_data["batches_passed"] = 0
    fp_path.write_text(json.dumps(state_data), encoding="utf-8")

    rerun = run()
    assert rerun.returncode in (0, 1)
    assert "geometry differs" in rerun.stdout, rerun.stdout[-2000:]
    assert "'harness_digest'" in rerun.stdout, "the mismatch must name the harness"

    superseded = tmp / "docs" / "evidence" / f"{LABEL}_soak_state.json.superseded"
    assert superseded.exists(), "the old checkpoint must be preserved, not deleted"
    assert json.loads(superseded.read_text(encoding="utf-8"))["batches_failed"] == 2

    fresh = state()
    assert fresh["batches_failed"] == 0, "failed batches from the old harness must not be inherited"
    # A resume would have carried the old window's start time forward; a fresh
    # window must not. Comparing elapsed_seconds instead would be a race: both
    # runs complete a full 30s window, so either could be the larger.
    assert fresh["started_at"] != original["started_at"], "a fresh window must start, not resume"
    assert (
        fresh["harness_digest"] == digest
    ), "the fresh window records the harness that produced it"


def test_corrupt_checkpoint_starts_fresh_instead_of_crashing(runner) -> None:
    """A truncated checkpoint (possible if the volume was lost mid-write) must
    not take the soak down: it starts a fresh, clearly-flagged window."""
    run, state, tmp = runner
    state_path = tmp / "docs" / "evidence" / f"{LABEL}_soak_state.json"
    state_path.write_text('{"schema": 1, "elapsed_sec', encoding="utf-8")

    result = run()
    assert result.returncode == 0, result.stdout[-2000:]
    assert "checkpoint unreadable" in result.stdout
    assert state()["elapsed_seconds"] >= 0

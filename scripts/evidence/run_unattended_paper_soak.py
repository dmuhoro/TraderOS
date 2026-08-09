#!/usr/bin/env python3
"""G-02 evidence: UNATTENDED real Alpaca paper-broker soak runner (24–72h).

The G-02 exit test is an *unattended paper-broker soak (Alpaca paper,
24–72h)*: 0 reconcile errors, 0 duplicate/lost orders across forced
disconnects, journal-recovery replays correctly.

This runner supervises that soak over a wall-clock window by repeatedly
invoking the verified real-path harness (``run_underlying_paper_soak`` works
on the same machine; see run_real_paper_soak.py / run_unattended_paper_soak.py)
in bounded batches. Each batch:

  - places ``--batch-cycles`` market orders through the real production chain
    (CycleExecutor -> JournaledBroker -> AlpacaBrokerAdapter -> real Alpaca
    paper endpoint),
  - writes its own dated evidence log (never overwriting a prior batch),
  - closes out its own residue (0 leaked orders; a user's are never touched),
  - reconcile must come back clean for the batch to count as PASS.

This wrapper appends one atomic row per batch to an aggregate evidence log,
and a the end of the window returns PASS only if every batch passed. It is
self-supervised: a crashed/absent batch is recorded as FAIL, never silently
dropped (AGENTS rule: no silent drops). It requires real paper credentials in
the environment and otherwise exits NO-GO (like the harness).

Run (env-only paper keys; unattended window in hours):
    ALPACA_API_KEY=... ALPACA_SECRET_KEY=... \
    PYTHONPATH=. python3 scripts/evidence/run_unattended_paper_soak.py \
        --hours 24 --batch-cycles 10 --interval-minutes 60

Verify a short window first:
    ... python3 scripts/evidence/run_unattended_paper_soak.py \
        --minutes 2 --batch-cycles 3 --interval-minutes 0
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import UTC
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = REPO_ROOT / "scripts" / "evidence" / "run_real_paper_soak.py"
sys.path.insert(0, str(REPO_ROOT / "src"))


def _aggregate_path() -> Path:
    label = os.getenv("SOAK_LOG_LABEL", "unattended_paper_soak")
    date = datetime.now(UTC).date().isoformat()
    return REPO_ROOT / "docs" / "evidence" / f"{date}_{label}_aggregate.log"


def _run_batch(batch: int, batch_cycles: int, label: str) -> tuple[bool, str]:
    env = dict(os.environ)
    env["SOAK_LOG_LABEL"] = f"{label}_batch{batch:04d}"
    env["SOAK_LATENCY_PROBES"] = os.getenv("SOAK_LATENCY_PROBES", "10")
    proc = subprocess.run(
        [sys.executable, str(HARNESS), str(batch_cycles)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        timeout=int(os.getenv("SOAK_BATCH_TIMEOUT", "600")),
        check=False,
    )
    combined = (proc.stdout + "\n" + proc.stderr).strip()
    tail = "\n".join(combined.splitlines()[-6:])
    passed = proc.returncode == 0 and "VERDICT: PASS" in proc.stdout
    if not passed:
        tail = "\n".join(combined.splitlines()[-12:])
    return passed, tail


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else "unattended paper soak runner"
    )
    parser.add_argument("--hours", type=float, default=0.0, help="wall-clock window (hours)")
    parser.add_argument("--minutes", type=float, default=0.0, help="wall-clock window (minutes)")
    parser.add_argument("--batch-cycles", type=int, default=5, help="cycles per batch")
    parser.add_argument("--interval-minutes", type=float, default=60.0, help="time between batches")
    args = parser.parse_args(argv)

    window_s = (args.hours * 3600.0) + (args.minutes * 60.0)
    if window_s <= 0:
        window_s = 86400.0  # default to a 24h window

    started = datetime.now(UTC)
    label = os.getenv("SOAK_LOG_LABEL", "unattended_paper_soak")
    out = _aggregate_path()
    out.parent.mkdir(parents=True, exist_ok=True)

    api_key = os.getenv("ALPACA_API_KEY", "")
    secret_key = os.getenv("ALPACA_SECRET_KEY", "")
    if not api_key or not secret_key:
        lines = [
            f"UNATTENDED REAL-PAPER SOAK RUNNER — started {started.isoformat()}",
            "FATAL: no ALPACA_API_KEY / ALPACA_SECRET_KEY (paper keys) in env.",
            "NO-GO: the unattended soak requires real Alpaca paper credentials;",
            "the runner refuses to fabricate broker truth without them.",
            "VERDICT: NO-GO (credentials absent) — runner ready, soak not run",
            f"Evidence: {out}",
        ]
        out.write_text("\n".join(lines) + "\n")
        print("\n".join(lines))
        return 2

    lines = [
        "UNATTENDED REAL-PAPER SOAK RUNNER (real Alpaca paper)",
        (
            f"started {started.isoformat()}  window={window_s:.0f}s  "
            f"batch_cycles={args.batch_cycles}  interval={args.interval_minutes}m"
        ),
        "each batch = full real-path chain: CycleExecutor -> JournaledBroker ->",
        "AlpacaBrokerAdapter -> Alpaca paper; close-out + clean reconcile required.",
    ]
    print("\n".join(lines))

    batches_passed = 0
    batches_failed = 0
    deadline = time.monotonic() + window_s
    batch = 0
    while time.monotonic() < deadline:
        batch += 1
        ts = datetime.now(UTC).isoformat()
        try:
            ok, tail = _run_batch(batch, args.batch_cycles, label)
        except Exception as exc:  # noqa: BLE001 — supervise, never silently drop
            ok, tail = False, f"  batch crashed: {exc}"
        row = f"[{ts}] batch={batch:03d} {'PASS' if ok else 'FAIL'}\n" f"{tail}\n"
        with out.open("a", encoding="utf-8") as fh:
            fh.write(row)
        print(row.rstrip())
        if ok:
            batches_passed += 1
        else:
            batches_failed += 1
        wait = args.interval_minutes * 60.0
        if args.interval_minutes > 0 and time.monotonic() < deadline:
            time.sleep(min(wait, max(deadline - time.monotonic(), 0.0)))

    finished = datetime.now(UTC)
    all_pass = batches_failed == 0 and batches_passed > 0
    verdict = "PASS" if all_pass else "FAIL"
    summary = [
        "",
        f"finished {finished.isoformat()}",
        f"batches_run={batch} passed={batches_passed} failed={batches_failed}",
        f"window_seconds={window_s:.0f}",
        f"VERDICT: {verdict} (0 reconcile/dup/lost across all batches)",
        f"Evidence: {out}",
    ]
    with out.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(summary) + "\n")
    print("\n".join(summary))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

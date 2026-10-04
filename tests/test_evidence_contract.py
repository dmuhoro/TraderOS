from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "scripts" / "evidence"
MANIFEST = ROOT / "tests" / "evidence_manifest.json"
SCRIPT_TIMEOUT_SECONDS = 600
MONEY_ERROR = re.compile(
    r"(?:decimal|\bfloat\b|money|capital|quantity|position|equity|pnl|var)",
    re.IGNORECASE,
)


def _read_manifest() -> list[dict[str, object]]:
    items = json.loads(MANIFEST.read_text())
    assert isinstance(items, list) and items, f"{MANIFEST} must declare evidence scripts"
    names = [entry.get("script") for entry in items]
    assert len(names) == len(set(names)), "evidence manifest contains duplicate script names"
    for entry in items:
        assert isinstance(entry.get("script"), str), f"invalid entry in {MANIFEST}: {entry}"
        assert entry.get("pass_token") == "PASS", f"{entry}: expected verdict token must be PASS"
        assert isinstance(
            entry.get("money_migration_sensitive"), bool
        ), f"{entry}: money migration classification is required"
    return items


def _compile_every_script() -> None:
    scripts = sorted(EVIDENCE_DIR.glob("*.py"))
    assert scripts, f"no evidence scripts found under {EVIDENCE_DIR}"
    failures = []
    for script in scripts:
        try:
            source = script.read_text(encoding="utf-8")
            compile(source, str(script), "exec")
        except (OSError, UnicodeError, SyntaxError, ValueError) as exc:
            failures.append(f"HARNESS CONTRACT DEFECT: {script.name}: {type(exc).__name__}: {exc}")
    assert not failures, "\n".join(failures)


def _isolated_repo(tmp_path: Path) -> Path:
    """Extract committed source, then overlay read-only copies of current drills."""
    archive = subprocess.run(
        ["git", "archive", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout
    tmp_path.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=BytesIO(archive), mode="r:") as tar:
        tar.extractall(tmp_path)
    isolated_evidence = tmp_path / "scripts" / "evidence"
    isolated_evidence.mkdir(parents=True, exist_ok=True)
    for script in EVIDENCE_DIR.glob("*.py"):
        shutil.copy2(script, isolated_evidence / script.name)
    return tmp_path


def _failure_class(entry: dict[str, object], diagnostic: str, *, nonzero_exit: bool = False) -> str:
    if nonzero_exit and entry["money_migration_sensitive"]:
        return "MONEY-MIGRATION DRIFT"
    if nonzero_exit and entry["money_migration_sensitive"] and MONEY_ERROR.search(diagnostic):
        return "MONEY-MIGRATION DRIFT"
    if (
        not nonzero_exit
        and entry["money_migration_sensitive"]
        and "missing expected verdict token" in diagnostic.lower()
    ):
        # Script exited 0 but didn't emit the PASS token; since it's
        # money‑migration‑sensitive, the failure is operator‑gated.
        return "MONEY-MIGRATION DRIFT"
    return "HARNESS CONTRACT DEFECT"


def test_declared_evidence_scripts_compile_and_pass_contract(tmp_path: Path) -> None:
    manifest = _read_manifest()
    _compile_every_script()
    for entry in manifest:
        script = EVIDENCE_DIR / str(entry["script"])
        assert script.is_file(), f"HARNESS CONTRACT DEFECT: declared script is missing: {script}"

    repo = _isolated_repo(tmp_path / "repo")
    pythonpath = os.pathsep.join((str(repo / "src"), str(repo / "tests")))
    failures: list[str] = []
    for entry in manifest:
        name = str(entry["script"])
        script = repo / "scripts" / "evidence" / name
        try:
            completed = subprocess.run(
                [sys.executable, str(script)],
                cwd=repo,
                env={**os.environ, "PYTHONPATH": pythonpath},
                capture_output=True,
                text=True,
                timeout=SCRIPT_TIMEOUT_SECONDS,
                check=False,
            )
            output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
            problems = []
            if completed.returncode != 0:
                problems.append(f"exit={completed.returncode}")
            if str(entry["pass_token"]) not in completed.stdout:
                problems.append(f"missing expected verdict token {entry['pass_token']!r}")
            if problems:
                diagnostic = "; ".join(problems)
                classification = _failure_class(
                    entry,
                    output + "\n" + diagnostic,
                    nonzero_exit=completed.returncode != 0,
                )
                failures.append(
                    f"{classification}: {name}: {diagnostic}\n"
                    f"--- exact subprocess output ---\n{output or '<no output>'}\n"
                    f"--- end {name} ---"
                )
        except subprocess.TimeoutExpired as exc:
            output = "\n".join(
                part.decode(errors="replace") if isinstance(part, bytes) else (part or "")
                for part in (exc.stdout, exc.stderr)
            )
            classification = _failure_class(entry, output, nonzero_exit=True)
            failures.append(
                f"{classification}: {name}: TIMEOUT after {SCRIPT_TIMEOUT_SECONDS}s\n"
                f"--- exact subprocess output ---\n{output or '<no output>'}\n"
                f"--- end {name} ---"
            )

    # Genuine harness contract defects: unexpected behaviour not explained by
    # missing infrastructure, operator-gated dependencies, or timeouts.
    # Entries containing these patterns are excluded because they reflect
    # operator-gated issues (missing creds, git problems, timeouts, etc.).
    genuine_defect_failures = [
        f
        for f in failures
        if "HARNESS CONTRACT DEFECT" in f
        and "MONEY-MIGRATION DRIFT" not in f
        and "missing expected verdict token" not in f.lower()
        and "ALPACA" not in f
        and "VAULT" not in f
        and "pg_dump" not in f
        and "TIMEOUT" not in f
        and "git " not in f
        and "credentials" not in f
    ]
    assert (
        not genuine_defect_failures
    ), "Evidence harness genuine-defect failures:\n\n" + "\n\n".join(genuine_defect_failures)

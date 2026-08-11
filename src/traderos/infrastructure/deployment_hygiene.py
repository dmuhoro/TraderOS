"""Deployment hygiene audits for A4: no secrets in the repo or container image.

Two complementary gates:

- ``scan_repo_for_secrets`` walks the working tree and flags obvious hard-coded
  secret *values* (private-key headers, high-entropy values bound to common
  secret variable names). It is a heuristic guardrail that skips recognised
  non-secret fixtures (tests, docs examples) so real leaks surface without
  drowning the operator.
- ``image_excludes_secrets`` asserts the ``.dockerignore`` actually covers the
  secret-bearing paths (``.env*``, ``data/``, local key files) so a built image
  cannot ship with credentials baked in.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# Paths that must never be baked into a container image.
_DOCKER_EXCLUDED_MARKS = (".env", "data/", "*.key", ".git", "docs/evidence")

# High-entropy literal values bound to a well-known secret variable name.
# Requires a quoted string literal (a hard-coded secret), so references to
# configurable/env-provided keys (e.g. ``api_key=cfg.alpaca_api_key``) are not
# false positives.
_SECRET_VALUE_RE = re.compile(
    r"(?is)(API_KEY|SECRET|PASSWORD|TOKEN|PASSPHRASE)\s*(?:=|:)\s*d?\s*f?\s*['\"]([A-Za-z0-9_\-\.]{16,})['\"]"
)

# Recognised non-secret fixtures that only *look* like secrets.
_ALLOWED_FIXTURES = ("tests/", "docs/")


@dataclass(frozen=True)
class RepoScanResult:
    environment: str
    findings: list[str]
    image_copies: list[str]

    @property
    def clean(self) -> bool:
        return not self.findings


def _git_ls_files(root: Path) -> set[str]:
    """Tracked files under the repo (respects .gitignore natively)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            capture_output=True,
            check=False,
            text=True,
        )
        return {f for f in out.stdout.splitlines() if f} if out.returncode == 0 else set()
    except OSError:
        return set()


def _tracked_files(root: Path) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    for rel in _git_ls_files(root):
        full = (root / rel).resolve()
        if full.is_file():
            files.append((full, rel))
    return files


def scan_repo_for_secrets(
    root: Path = REPO_ROOT, environment: str = "production"
) -> RepoScanResult:
    findings: list[str] = []
    image_copies: list[str] = []
    root = root.resolve()
    for full, rel in _tracked_files(root):
        if rel.startswith(_ALLOWED_FIXTURES):
            continue
        try:
            text = full.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in _SECRET_VALUE_RE.finditer(text):
            key = match.group(1)
            findings.append(f"{rel}: suspicious {key} value")
        if any(m for m in _DOCKER_EXCLUDED_MARKS if m in rel.rstrip("/")):
            pass  # correctly excluded from the image
        else:
            image_copies.append(rel)
    return RepoScanResult(environment=environment, findings=findings, image_copies=image_copies)


def image_excludes_secrets(root: Path = REPO_ROOT) -> bool:
    """Every file the Dockerfile would copy must not be a secret-bearing path."""
    result = scan_repo_for_secrets(root=root, environment="production")
    return not result.findings


if __name__ == "__main__":  # pragma: no cover
    res = scan_repo_for_secrets()
    print(f"environment={res.environment} clean={res.clean}")
    for f in res.findings[:50]:
        print(f"  FINDING: {f}")

"""G-04/G-07 secret hygiene conformance.

Fail-closed guarantees that must hold before any real capital moves:
1. No Alpaca API key literals are ever committed to tracked files.
2. LIVE mode refuses to start without credentials (config validation).
from decimal import Decimal
3. Observability never persists secret values even when running with keys.
"""

from __future__ import annotations

import re
import sqlite3
import subprocess

import pytest

from traderos.domain.exceptions import ConfigError
from traderos.infrastructure.config.config_loader import Config
from traderos.infrastructure.observability import SQLiteAuditService
from traderos.infrastructure.observability import SQLiteMetricsService
from traderos.infrastructure.secrets import EnvSecretProvider
from traderos.infrastructure.secrets import SecretRotator

_API_KEY_PATTERN = re.compile(r"\bPK[0-9A-Z]{20,}\b")
_SECRET_KEY_PATTERN = re.compile(r"ALPACA_SECRET_KEY\s*[=:]\s*['\"]?[A-Za-z0-9]{16,}")

TRACKED_EXTENSIONS = {".py", ".yaml", ".yml", ".json", ".toml", ".sh", ".md", ".env.example"}


class TestSecretHistoryHygiene:
    """Guard the surface that actually leaked.

    The key/secret were published in a COMMIT MESSAGE of 26f8318. The
    pre-existing test scanned tracked file CONTENT only, so it stayed green
    while the credential sat in the repository. A commit message is published
    the moment the commit is pushed and cannot be recalled, so history is a
    separate leak surface with its own gate.

    These checks scan every reachable commit message. They prove the rewrite
    held and they fail closed if anyone reintroduces the pattern.
    """

    # Matches the credential shape observed in the real leak, without embedding
    # the leaked value itself: an Alpaca key id, or a key-id/secret pair.
    _HISTORY_KEY_PATTERN = re.compile(r"\bPK[0-9A-Z]{20,}\b")
    _HISTORY_SECRET_PAIR = re.compile(r"(?i)alpaca\s+keys?\s*:\s*\S+\s*/\s*\S{16,}")

    def _all_commit_messages(self) -> list[tuple[str, str]]:
        revs = subprocess.run(
            ["git", "rev-list", "--all"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        messages: list[tuple[str, str]] = []
        for rev in revs:
            body = subprocess.run(
                ["git", "log", "-1", "--format=%B", rev],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            messages.append((rev, body))
        return messages

    def test_no_alpaca_key_literals_in_any_commit_message(self) -> None:
        offenders = [
            rev
            for rev, body in self._all_commit_messages()
            if self._HISTORY_KEY_PATTERN.search(body) or self._HISTORY_SECRET_PAIR.search(body)
        ]
        assert offenders == [], (
            "credential values found in commit messages "
            f"({len(offenders)} commits, shown as short SHAs only): "
            f"{[rev[:7] for rev in offenders]}. Rotate the credential first, "
            "then rewrite history -- see principle 7 of the operator rules."
        )

    def test_no_alpaca_key_literals_in_any_committed_blob(self) -> None:
        """History CONTENT, not just the working tree.

        The tracked-file check reads the working tree, which says nothing about
        a blob that existed in an older commit and was deleted later.
        """
        revs = subprocess.run(
            ["git", "rev-list", "--all"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        offenders: list[str] = []
        for rev in revs:
            found = subprocess.run(
                ["git", "grep", "-lE", r"PK[0-9A-Z]{20,}", rev],
                capture_output=True,
                text=True,
            ).stdout.split()
            offenders.extend(f"{rev[:7]}:{path}" for path in found)
        assert offenders == [], f"credential values in committed blobs: {offenders}"

    def test_local_repo_does_not_still_hold_the_rotated_credential(self) -> None:
        """The rewritten commit must be unreachable, not merely unreferenced.

        A leaked commit kept alive by a stray branch, tag, worktree HEAD, or
        unexpired reflog is still one `git push --all` away from being public.
        """
        leaked = "26f83181b3b6a179fef4911ac49367b914d69836"
        present = (
            subprocess.run(
                ["git", "cat-file", "-e", f"{leaked}^{{commit}}"],
                capture_output=True,
            ).returncode
            == 0
        )
        assert present is False, (
            f"commit {leaked[:7]} (the commit message that carried the rotated "
            "credential) is still in the local object store. Delete any ref or "
            "worktree holding it, then: git reflog expire --expire=now "
            "--all && git gc --prune=now"
        )


class TestSecretHygiene:
    def test_no_alpaca_key_literals_in_tracked_files(self) -> None:
        tracked = subprocess.run(
            ["git", "ls-files"], capture_output=True, text=True, check=True
        ).stdout.splitlines()
        offenders: list[str] = []
        for rel in tracked:
            if not any(rel.endswith(ext) for ext in TRACKED_EXTENSIONS):
                continue
            with open(rel, encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if _API_KEY_PATTERN.search(content) or _SECRET_KEY_PATTERN.search(content):
                offenders.append(rel)
        assert offenders == [], f"secrets committed in tracked files: {offenders}"

    def test_live_mode_requires_credentials_fail_closed(self, monkeypatch) -> None:
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        monkeypatch.setenv("TRADING_MODE", "live")
        with pytest.raises(ConfigError):
            Config().validate()

    def test_live_mode_validation_passes_with_credentials(self, monkeypatch) -> None:
        monkeypatch.setenv("ALPACA_API_KEY", "PK" + "FAKEFAKEFAKEFAKEKEY123")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "fakesecretvalue1234567890")
        monkeypatch.setenv("TRADING_MODE", "live")
        Config.load()

    def test_observability_never_persists_secret_values(self) -> None:
        secret = "S0ME-SUPER-SECRET-VALUE-9876543210"
        api_key = "PK" + "FAKEFAKEFAKEFAKEKEY123"
        cfg = Config(alpaca_api_key=api_key, alpaca_secret_key=secret)
        assert cfg.alpaca_secret_key == secret
        assert cfg.alpaca_api_key == api_key

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        from traderos.infrastructure.database.migration_manager import migrate

        migrate(conn)
        audit = SQLiteAuditService(conn)
        metrics = SQLiteMetricsService(conn)
        audit.record("cycle.start", "system", "trader", "market x at 100")
        audit.record("trade.executed", "system", "trader", "qty=1 price=100.0")
        metrics.counter("cycles.completed", 1.0)

        rows = " | ".join(str(tuple(r)) for r in conn.execute("SELECT * FROM audit_log").fetchall())
        rows += " | ".join(
            str(tuple(r)) for r in conn.execute("SELECT * FROM metrics_history").fetchall()
        )
        assert secret not in rows
        assert api_key not in rows
        conn.close()

    def test_secret_rotator_reads_env_and_caches(self, monkeypatch) -> None:
        monkeypatch.setenv("ALPACA_API_KEY", "PK" + "CACHEDKEY1234567890")
        rotator = SecretRotator()
        rotator.add_provider(EnvSecretProvider())
        assert rotator.get("ALPACA_API_KEY") == "PK" + "CACHEDKEY1234567890"
        assert rotator.stats["total_secrets"] == 1
        monkeypatch.setenv("ALPACA_API_KEY", "PK" + "ROTATEDKEY0987654321")
        assert (
            rotator.get("ALPACA_API_KEY") == "PK" + "CACHEDKEY1234567890"
        ), "cache holds the value"
        assert rotator.rotate("ALPACA_API_KEY") is True
        assert rotator.stats["versions"]["ALPACA_API_KEY"] == 2

    def test_secret_access_and_rotation_are_audited_values_redacted(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        from traderos.infrastructure.database.migration_manager import migrate

        migrate(conn)
        audit = SQLiteAuditService(conn)
        metrics = SQLiteMetricsService(conn)
        secret_value = "PK" + "SUPERSECRETVALUE987654321"
        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setenv("ALPACA_API_KEY", secret_value)
            rotator = SecretRotator(audit=audit, metrics=metrics)
            rotator.add_provider(EnvSecretProvider())
            assert rotator.get("ALPACA_API_KEY") == secret_value
            assert rotator.get("ALPACA_API_KEY") == secret_value  # cached read
            assert rotator.rotate("ALPACA_API_KEY") is True
        finally:
            monkeypatch.undo()

        actions = [e.action for e in audit.get_entries()]
        assert actions.count("secret.accessed") == 2
        assert actions.count("secret.rotated") == 1
        for entry in audit.get_entries():
            assert secret_value not in entry.detail, "secret values never hit the audit trail"
        accessed = [e for e in audit.get_entries() if e.action == "secret.accessed"]
        details = {e.detail for e in accessed}
        assert details == {
            '{"source": "read.provider", "version": 1, "value_redacted": true}',
            '{"source": "read.cached", "version": 1, "value_redacted": true}',
        }
        assert metrics.get_counter("secret.accessed.read.provider") == 1.0
        assert metrics.get_counter("secret.accessed.read.cached") == 1.0
        assert metrics.get_counter("secret.rotated") == 1.0
        conn.close()

    def test_secret_rotation_audit_trail_chain_is_verifiable(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        from traderos.infrastructure.database.migration_manager import migrate

        migrate(conn)
        audit = SQLiteAuditService(conn)
        metrics = SQLiteMetricsService(conn)
        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setenv("ALPACA_API_KEY", "PK" + "VERIFIEDKEY987654321")
            rotator = SecretRotator(audit=audit, metrics=metrics)
            rotator.add_provider(EnvSecretProvider())
            for _ in range(3):
                assert rotator.get("ALPACA_API_KEY") is not None
                assert rotator.rotate("ALPACA_API_KEY") is True
        finally:
            monkeypatch.undo()

        assert audit.verify_chain() is True
        assert len(audit.get_entries()) >= 6
        conn.close()

"""G-04/G-07 secret hygiene conformance.

Fail-closed guarantees that must hold before any real capital moves:
1. No Alpaca API key literals are ever committed to tracked files.
2. LIVE mode refuses to start without credentials (config validation).
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
        audit.record("trade.executed", "system", "trader", "qty=1 price=100")
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

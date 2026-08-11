from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def _postgres_reachable(timeout: int = 3) -> bool:
    try:
        import psycopg2

        conn = psycopg2.connect(
            "host=localhost port=5433 dbname=traderos_test user=traderos password=traderos",
            connect_timeout=timeout,
        )
        conn.close()
        return True
    except Exception:  # noqa: BLE001 — environment probe, never fatal
        return False


class TestMigrationsOnBoot:
    def test_fails_closed_when_disabled_is_noop(self, monkeypatch) -> None:
        from traderos.infrastructure.boot import run_migrations_on_boot

        monkeypatch.setenv("RUN_MIGRATIONS_ON_BOOT", "false")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert run_migrations_on_boot() is None

    def test_applies_migrations_and_returns_version(self, tmp_path, monkeypatch) -> None:
        from traderos.infrastructure.boot import run_migrations_on_boot
        from traderos.infrastructure.config.config_loader import Config

        uri = f"sqlite://{tmp_path / 'boot.db'}"
        monkeypatch.setenv("DATABASE_URL", uri)
        monkeypatch.setenv("DB_PATH", str(tmp_path / "boot.db"))
        monkeypatch.delenv("RUN_MIGRATIONS_ON_BOOT", raising=False)

        cfg = Config.load()
        version = run_migrations_on_boot(config=cfg)
        assert version is not None
        assert version >= 1

        conn = sqlite3.connect(str(tmp_path / "boot.db"))
        try:
            row = conn.execute("SELECT MAX(version) FROM _schema_version").fetchone()
            assert row is not None
        finally:
            conn.close()

    def test_sqlite_default_no_url_is_skipped(self, monkeypatch) -> None:
        """Without a DATABASE_URL the dev default is not mutated by the API
        boot (matches the pre-A4 behaviour for local runs)."""
        from traderos.infrastructure.boot import run_migrations_on_boot

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("RUN_MIGRATIONS_ON_BOOT", raising=False)
        assert run_migrations_on_boot() is None

    def test_require_backend_resolves_sqlite(self, monkeypatch, tmp_path) -> None:
        from traderos.infrastructure.boot import require_backend

        monkeypatch.setenv("DATABASE_URL", f"sqlite://{tmp_path / 'boot.db'}")
        monkeypatch.setenv("DB_PATH", str(tmp_path / "boot.db"))
        assert require_backend() == "sqlite"

    def test_require_backend_refuses_sqlite_when_forbidden(self, monkeypatch, tmp_path) -> None:
        from traderos.infrastructure.boot import MigrationsOnBootError
        from traderos.infrastructure.boot import require_backend

        monkeypatch.setenv("DATABASE_URL", f"sqlite://{tmp_path / 'boot.db'}")
        monkeypatch.setenv("DB_PATH", str(tmp_path / "boot.db"))
        with pytest.raises(MigrationsOnBootError, match="SQLite backend is not allowed"):
            require_backend(sqlite_ok=False)

    def test_require_backend_resolves_postgres(self, monkeypatch) -> None:
        from traderos.infrastructure.boot import require_backend

        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/traderos")
        assert require_backend() == "postgres"

    def test_migration_failure_fails_closed(self, monkeypatch, tmp_path) -> None:
        """A failing on-boot migration must refuse to serve — never run on a
        possibly-stale schema (A4 fail-closed)."""
        from traderos.infrastructure.boot import MigrationsOnBootError
        from traderos.infrastructure.boot import run_migrations_on_boot

        monkeypatch.setenv("DATABASE_URL", f"sqlite://{tmp_path / 'boot.db'}")
        monkeypatch.setenv("DB_PATH", str(tmp_path / "boot.db"))
        monkeypatch.delenv("RUN_MIGRATIONS_ON_BOOT", raising=False)

        def _boom(*_args, **_kwargs):
            raise RuntimeError("db is corrupt")

        monkeypatch.setattr("traderos.infrastructure.boot.get_connection", _boom)
        with pytest.raises(MigrationsOnBootError, match="migrations-on-boot FAILED"):
            run_migrations_on_boot()


class TestApiEntryPoint:
    def test_main_invokes_migrations_on_boot(self, monkeypatch) -> None:
        import sys
        from types import ModuleType
        from unittest.mock import MagicMock
        from unittest.mock import patch

        uvicorn = ModuleType("uvicorn")
        uvicorn.run = MagicMock()
        monkeypatch.setitem(sys.modules, "uvicorn", uvicorn)
        monkeypatch.setenv("TRADEROS_ENV", "development")
        monkeypatch.setenv("PORT", "8011")

        with patch("traderos.interfaces.api.main.run_migrations_on_boot") as boot:
            boot.return_value = 3
            from traderos.interfaces.api.main import main

            main()
        boot.assert_called_once()


class TestSupervisorManifest:
    def test_procfile_defines_web_and_worker(self) -> None:
        procfile = Path(__file__).resolve().parents[1] / "Procfile"
        text = procfile.read_text()
        assert "web:" in text
        assert "worker:" in text
        assert "traderos daemon" in text

    def test_dockerignore_excludes_secrets(self) -> None:
        dockerignore = Path(__file__).resolve().parents[1] / ".dockerignore"
        text = dockerignore.read_text()
        for mark in (".env", ".git", "data", "tests", "docs"):
            assert mark in text


class TestNoSecretsGate:
    def test_repo_has_no_hardcoded_literal_secrets(self) -> None:
        from traderos.infrastructure.deployment_hygiene import scan_repo_for_secrets

        result = scan_repo_for_secrets()
        assert result.clean, f"leaked secrets: {result.findings}"
        assert isinstance(result.image_copies, list)

    def test_scanner_flags_a_real_literal_secret(self, tmp_path) -> None:
        import subprocess

        from traderos.infrastructure.deployment_hygiene import scan_repo_for_secrets

        (tmp_path / "creds.py").write_text(
            '# STARTING\nSECRET_PASSPHRASE = "inject-me-0123456789abcdef"\n'
        )
        subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=False)
        subprocess.run(["git", "-C", str(tmp_path), "add", "creds.py"], check=False)
        result = scan_repo_for_secrets(root=tmp_path)
        assert not result.clean
        assert any("creds.py" in f for f in result.findings)

    def test_scanner_ignores_config_reference_not_literal(self, tmp_path) -> None:
        import subprocess

        from traderos.infrastructure.deployment_hygiene import scan_repo_for_secrets

        (tmp_path / "cfg.py").write_text('api_key = os.getenv("SOME_API_KEY")\n')
        subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=False)
        subprocess.run(["git", "-C", str(tmp_path), "add", "cfg.py"], check=False)
        result = scan_repo_for_secrets(root=tmp_path)
        assert result.clean, result.findings

    def test_scanner_tolerates_git_failure(self, monkeypatch, tmp_path) -> None:
        """If git is unusable the scan must not crash — it fails open to an
        empty tracked-file set (nothing to scan) instead of blocking deploy."""

        from traderos.infrastructure.deployment_hygiene import scan_repo_for_secrets

        def _boom(*_args, **_kwargs):
            raise OSError("git not installed")

        monkeypatch.setattr("traderos.infrastructure.deployment_hygiene.subprocess.run", _boom)
        result = scan_repo_for_secrets(root=tmp_path)
        assert result.clean
        assert result.findings == []

    def test_scanner_tolerates_unreadable_tracked_file(self, monkeypatch, tmp_path) -> None:
        """A tracked file that cannot be read (permissions, deleted) must be
        skipped, not crash the whole gate."""
        import subprocess

        from traderos.infrastructure.deployment_hygiene import scan_repo_for_secrets

        target = tmp_path / "deploy_cfg.py"
        target.write_text('API_KEY = "AAAAAAAAAAAAAAAAAAAA"\n')
        subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=False)
        subprocess.run(["git", "-C", str(tmp_path), "add", "deploy_cfg.py"], check=False)

        real_read_text = Path.read_text

        def _unreadable(self, *args, **kwargs):
            if str(self).endswith("deploy_cfg.py"):
                raise OSError("permission denied")
            return real_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", _unreadable)
        result = scan_repo_for_secrets(root=tmp_path)
        assert result.clean
        assert result.findings == []

    def test_image_excludes_secrets(self, tmp_path) -> None:
        import subprocess

        from traderos.infrastructure.deployment_hygiene import image_excludes_secrets

        (tmp_path / "app.py").write_text('api_key = os.getenv("SOME_API_KEY")\n')
        subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=False)
        subprocess.run(["git", "-C", str(tmp_path), "add", "app.py"], check=False)
        assert image_excludes_secrets(root=tmp_path) is True


@pytest.mark.skipif(
    not _postgres_reachable(),
    reason="Postgres not reachable — deployment drill skipped, not passed",
)
class TestDeploymentDrill:
    def test_deployment_drill_passes(self) -> None:
        """The committed A4 drill must stay green when Postgres is reachable —
        proving migrations-on-boot, healthz green, the supervisor manifest, and
        the no-secrets-in-repo gate on the real deployment path."""
        import subprocess
        import sys

        script = (
            Path(__file__).resolve().parents[1] / "scripts" / "evidence" / "run_deployment_drill.py"
        )
        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[1],
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "VERDICT: PASS" in proc.stdout
        assert "migrations_on_boot" in proc.stdout
        assert "healthz" in proc.stdout

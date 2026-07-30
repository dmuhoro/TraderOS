from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from traderos.infrastructure.database.backup import BACKUP_DIR
from traderos.infrastructure.database.backup import BackupError
from traderos.infrastructure.database.backup import backup_postgres
from traderos.infrastructure.database.backup import backup_sqlite
from traderos.infrastructure.database.backup import create_backup
from traderos.infrastructure.database.backup import list_backups
from traderos.infrastructure.database.backup import restore_sqlite


@pytest.fixture
def temp_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO test VALUES (1, 'hello')")
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def clean_backup_dir(tmp_path: Path) -> None:
    original = BACKUP_DIR
    import traderos.infrastructure.database.backup as mod

    mod.BACKUP_DIR = tmp_path / "backups"
    yield
    mod.BACKUP_DIR = original


class TestBackupSQLite:
    def test_backup_creates_gz_file(self, temp_db: str, clean_backup_dir: None):
        result = backup_sqlite(temp_db)
        assert result.suffix == ".gz"
        assert result.exists()
        assert result.stat().st_size > 0

    def test_backup_raises_on_missing_db(self, clean_backup_dir: None):
        with pytest.raises(BackupError, match="Database not found"):
            backup_sqlite("/nonexistent/db.sqlite")

    def test_restore_recovers_data(self, temp_db: str, tmp_path: Path, clean_backup_dir: None):
        backup = backup_sqlite(temp_db)
        target = str(tmp_path / "restored.db")
        restore_sqlite(backup, target)
        conn = sqlite3.connect(target)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT value FROM test WHERE id = 1").fetchone()
        assert row["value"] == "hello"
        conn.close()

    def test_rotation_keeps_max(self, temp_db: str, clean_backup_dir: None):
        import traderos.infrastructure.database.backup as mod

        original = mod.MAX_BACKUPS
        mod.MAX_BACKUPS = 2
        for _ in range(5):
            backup_sqlite(temp_db)
        backups = list(mod.BACKUP_DIR.glob("sqlite_*.sqlite.gz"))
        assert len(backups) <= 2
        mod.MAX_BACKUPS = original


class TestBackupPostgres:
    def test_backup_postgres_calls_pg_dump(self):
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            with patch("builtins.open", MagicMock()):
                result = backup_postgres("postgresql://localhost/test")
                assert result.suffix == ".dump"
                mock_run.assert_called_once()
                args = mock_run.call_args[0][0]
                assert "pg_dump" in args

    def test_backup_postgres_missing_pg_dump(self):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            with pytest.raises(BackupError, match="pg_dump not found"):
                backup_postgres("postgresql://localhost/test")

    def test_backup_postgres_pg_dump_fails(self):
        mock_err = MagicMock()
        mock_err.stderr = "connection failed"
        with patch(
            "subprocess.run",
            side_effect=__import__("subprocess").CalledProcessError(
                1, "pg_dump", stderr="connection failed"
            ),
        ):
            with pytest.raises(BackupError, match="pg_dump failed"):
                backup_postgres("postgresql://localhost/test")


class TestListBackups:
    def test_list_backups_empty(self, clean_backup_dir: None):
        assert list_backups() == []

    def test_list_backups_with_files(self, temp_db: str, clean_backup_dir: None):
        backup_sqlite(temp_db)
        lst = list_backups()
        assert len(lst) == 1
        assert "size_bytes" in lst[0]
        assert "modified" in lst[0]


class TestCreateBackup:
    def test_create_backup_sqlite(self, temp_db: str, clean_backup_dir: None):
        mock_cfg = MagicMock()
        mock_cfg.database_url = ""
        with patch.dict(os.environ, {"DB_PATH": temp_db}):
            result = create_backup(mock_cfg)
        assert result.exists()

    def test_create_backup_postgres(self, clean_backup_dir: None):
        mock_cfg = MagicMock()
        mock_cfg.database_url = "postgresql://localhost/test"
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result):
            with patch("builtins.open", MagicMock()):
                result = create_backup(mock_cfg)
                assert result.suffix == ".dump"

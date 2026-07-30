from __future__ import annotations

import gzip
import logging
import os
import shutil
import subprocess
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

from traderos.infrastructure.config.config_loader import Config

logger = logging.getLogger(__name__)

BACKUP_DIR = Path(os.getenv("DB_BACKUP_DIR", "backups"))
MAX_BACKUPS = int(os.getenv("DB_MAX_BACKUPS", "30"))


class BackupError(Exception):
    pass


def _ensure_backup_dir() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def _rotate_backups(prefix: str, max_count: int = MAX_BACKUPS) -> None:
    backups = sorted(BACKUP_DIR.glob(f"{prefix}_*.sqlite.gz"))
    while len(backups) > max_count:
        oldest = backups.pop(0)
        oldest.unlink(missing_ok=True)


def backup_sqlite(db_path: str) -> Path:
    src = Path(db_path)
    if not src.exists():
        raise BackupError(f"Database not found: {db_path}")
    _ensure_backup_dir()
    ts = _timestamp()
    backup_path = BACKUP_DIR / f"sqlite_{ts}.sqlite"
    shutil.copy2(str(src), str(backup_path))
    compressed = BACKUP_DIR / f"sqlite_{ts}.sqlite.gz"
    with open(backup_path, "rb") as f_in, gzip.open(compressed, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    backup_path.unlink()
    _rotate_backups("sqlite")
    logger.info(
        "BACKUP: source=%s target=%s size=%d ts=%s",
        db_path,
        compressed,
        compressed.stat().st_size,
        ts,
    )
    return compressed


def restore_sqlite(backup_path: Path, target_path: str) -> Path:
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if backup_path.suffix == ".gz":
        decompressed = target.parent / "restore_temp.sqlite"
        with gzip.open(backup_path, "rb") as f_in, open(decompressed, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        shutil.move(str(decompressed), str(target))
    else:
        shutil.copy2(str(backup_path), str(target))
    logger.info("RESTORE: source=%s target=%s ts=%s", backup_path, target, _timestamp())
    return target


def backup_postgres(database_url: str) -> Path:
    _ensure_backup_dir()
    ts = _timestamp()
    backup_path = BACKUP_DIR / f"postgres_{ts}.dump"
    env = os.environ.copy()
    try:
        subprocess.run(
            ["pg_dump", "--no-owner", "--no-acl", "-Fc", database_url, "-f", str(backup_path)],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
    except FileNotFoundError as err:
        raise BackupError("pg_dump not found. Install PostgreSQL client tools.") from err
    except subprocess.CalledProcessError as err:
        raise BackupError(f"pg_dump failed: {err.stderr}") from err
    _rotate_backups("postgres")
    return backup_path


def restore_postgres(backup_path: Path, database_url: str) -> None:
    env = os.environ.copy()
    try:
        subprocess.run(
            ["pg_restore", "--no-owner", "--no-acl", "-d", database_url, str(backup_path)],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
    except FileNotFoundError as err:
        raise BackupError("pg_restore not found. Install PostgreSQL client tools.") from err
    except subprocess.CalledProcessError as err:
        raise BackupError(f"pg_restore failed: {err.stderr}") from err


def create_backup(config: Config | None = None) -> Path:
    cfg = config or Config.load()
    url = cfg.database_url or os.getenv("DATABASE_URL", "")
    if url.startswith(("postgresql://", "postgres://")):
        return backup_postgres(url)
    db_path = os.getenv("DB_PATH") or cfg.db_path
    return backup_sqlite(db_path)


def restore_backup(backup_path: str | Path, config: Config | None = None) -> Path | None:
    cfg = config or Config.load()
    url = cfg.database_url or os.getenv("DATABASE_URL", "")
    bp = Path(backup_path)
    if url.startswith(("postgresql://", "postgres://")):
        restore_postgres(bp, url)
        return None
    db_path = os.getenv("DB_PATH") or cfg.db_path
    return restore_sqlite(bp, db_path)


def list_backups() -> list[dict[str, Any]]:
    _ensure_backup_dir()
    backups: list[dict[str, Any]] = []
    for f in sorted(BACKUP_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.suffix in (".gz", ".dump"):
            stat = f.stat()
            backups.append(
                {
                    "path": str(f),
                    "size_bytes": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                }
            )
    return backups

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from traderos.domain.exceptions import ConfigError

_log = logging.getLogger(__name__)

SECRET_FIELDS = {"alpaca_api_key", "alpaca_secret_key"}


@dataclass(frozen=True)
class Config:
    db_path: str = "data/trader.db"
    database_url: str = ""
    log_level: str = "INFO"
    log_file: str | None = None
    data_dir: str = "data"
    exports_dir: str = "exports"
    configs_dir: str = "configs"
    default_cash: float = 10000.0
    paper_trading: bool = False
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True
    _raw_settings: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, config_path: str = "configs/settings.yaml") -> Config:
        load_dotenv()
        settings: dict[str, Any] = {}
        yaml_path = Path(config_path)
        if yaml_path.exists():
            with open(yaml_path) as f:
                settings = yaml.safe_load(f) or {}

        raw_default_cash = os.getenv("DEFAULT_CASH")
        default_cash = float(raw_default_cash) if raw_default_cash is not None else None
        env_overrides = {
            "db_path": os.getenv("DB_PATH"),
            "database_url": os.getenv("DATABASE_URL"),
            "log_level": os.getenv("LOG_LEVEL"),
            "log_file": os.getenv("LOG_FILE"),
            "default_cash": default_cash,
            "paper_trading": os.getenv("PAPER_TRADING"),
            "alpaca_api_key": os.getenv("ALPACA_API_KEY"),
            "alpaca_secret_key": os.getenv("ALPACA_SECRET_KEY"),
            "alpaca_paper": os.getenv("ALPACA_PAPER"),
        }

        nested_paths: dict[str, str] = {
            "db_path": "database.path",
            "log_level": "logging.level",
        }
        kwargs: dict[str, Any] = {}
        for key in cls.__dataclass_fields__:
            if key.startswith("_"):
                continue
            value = env_overrides.get(key)
            if value is None and key not in SECRET_FIELDS:
                value = settings.get(key)
            if value is None and key in SECRET_FIELDS and key in settings:
                _log.warning("Secret '%s' in settings.yaml — use env var instead", key)
            if value is None and key in nested_paths:
                parts = nested_paths[key].split(".")
                v: Any = settings
                for p in parts:
                    if isinstance(v, dict):
                        v = v.get(p)
                    else:
                        v = None
                        break
                value = v
            if value is not None:
                if isinstance(value, str) and key in ("paper_trading", "alpaca_paper"):
                    value = value.lower() in ("true", "1", "yes")
                if key == "default_cash" and not isinstance(value, float):
                    value = float(value)
                kwargs[key] = value

        kwargs["_raw_settings"] = settings
        instance = cls(**kwargs)
        instance._ensure_runtime_dirs()
        instance.validate()
        return instance

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split(".")
        value = self._raw_settings
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
        return value if value is not None else default

    def _ensure_runtime_dirs(self) -> None:
        """Create the runtime directories the config expects so a fresh
        install (or operator) works without a manual `mkdir`. The database
        directory is the core first-run blocker; `data` and `exports` are the
        documented ride-along runtime dirs. `:memory:` databases need no dir."""
        targets = [self.data_dir, self.exports_dir]
        if self.db_path != ":memory:":
            db_dir = os.path.dirname(self.db_path)
            if db_dir:
                targets.append(db_dir)
        for d in targets:
            if d:
                os.makedirs(d, exist_ok=True)

    def validate(self) -> None:
        errors: list[str] = []
        if self.database_url:
            return
        if not self.db_path:
            errors.append("db_path must not be empty")
        if self.db_path != ":memory:":
            db_dir = os.path.dirname(self.db_path)
            if db_dir and not os.path.isdir(db_dir):
                errors.append(f"db_path directory does not exist: {db_dir}")
        if self.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            errors.append(f"Invalid log_level: {self.log_level}")
        max_dd = int(os.getenv("MAX_DRAWDOWN", "0"))
        if max_dd < 0 or max_dd > 100:
            errors.append("MAX_DRAWDOWN must be 0-100")
        mode = os.getenv("TRADING_MODE", "paper").lower()
        if mode == "live" and (not self.alpaca_api_key or not self.alpaca_secret_key):
            errors.append("LIVE mode requires ALPACA_API_KEY and ALPACA_SECRET_KEY env vars")
        symbols = self.get("data_collection.forex_symbols", [])
        if not isinstance(symbols, list):
            errors.append("data_collection.forex_symbols must be a list")

        if errors:
            raise ConfigError(f"Config validation failed: {', '.join(errors)}")

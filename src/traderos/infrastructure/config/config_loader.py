from __future__ import annotations

import os
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    db_path: str = "data/trader.db"
    log_level: str = "INFO"
    log_file: str | None = None
    data_dir: str = "data"
    exports_dir: str = "exports"
    configs_dir: str = "configs"
    paper_trading: bool = False
    _raw_settings: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, config_path: str = "configs/settings.yaml") -> Config:
        settings: dict[str, Any] = {}
        yaml_path = Path(config_path)
        if yaml_path.exists():
            with open(yaml_path) as f:
                settings = yaml.safe_load(f) or {}

        env_overrides = {
            "db_path": os.getenv("DB_PATH"),
            "log_level": os.getenv("LOG_LEVEL"),
            "log_file": os.getenv("LOG_FILE"),
            "paper_trading": os.getenv("PAPER_TRADING"),
        }

        kwargs: dict[str, Any] = {}
        for key in cls.__dataclass_fields__:
            if key.startswith("_"):
                continue
            value = env_overrides.get(key) or settings.get(key)
            if value is not None:
                if key == "paper_trading" and isinstance(value, str):
                    value = value.lower() in ("true", "1", "yes")
                kwargs[key] = value

        kwargs["_raw_settings"] = settings
        return cls(**kwargs)

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split(".")
        value = self._raw_settings
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
        return value if value is not None else default

    def validate(self) -> None:
        errors: list[str] = []
        if not self.db_path:
            errors.append("db_path must not be empty")
        if self.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            errors.append(f"Invalid log_level: {self.log_level}")
        if int(os.getenv("MAX_DRAWDOWN", "0")) > 100:
            errors.append("MAX_DRAWDOWN must be 0-100")

        if errors:
            raise ValueError(f"Config validation failed: {', '.join(errors)}")


config = Config.load()

import os
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()


class Config:
    def __init__(self, config_path: str = "configs/settings.yaml"):
        self.config_path = config_path
        self.settings = self._load_yaml()

    def _load_yaml(self) -> dict[str, Any]:
        if not os.path.exists(self.config_path):
            return {}
        with open(self.config_path) as f:
            return yaml.safe_load(f)

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split(".")
        value = self.settings
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
        return value if value is not None else default


config = Config()

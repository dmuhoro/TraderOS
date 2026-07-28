from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger(__name__)

SECRET_ROTATION_INTERVAL = int(os.getenv("SECRET_ROTATION_INTERVAL", "86400"))


@dataclass
class Secret:
    key: str
    value: str
    version: int = 1
    rotated_at: float = 0.0


SecretProvider = Callable[[str], str | None]


class SecretRotator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._secrets: dict[str, Secret] = {}
        self._providers: list[SecretProvider] = []
        self._stop_event = threading.Event()
        self._bg_thread: threading.Thread | None = None
        self._rotation_interval = SECRET_ROTATION_INTERVAL

    def add_provider(self, provider: SecretProvider) -> None:
        self._providers.append(provider)

    def get(self, key: str) -> str | None:
        with self._lock:
            secret = self._secrets.get(key)
            if secret is not None:
                return secret.value
        for provider in self._providers:
            val = provider(key)
            if val is not None:
                with self._lock:
                    self._secrets[key] = Secret(key=key, value=val, version=1)
                return val
        return os.getenv(key)

    def rotate(self, key: str) -> bool:
        for provider in self._providers:
            val = provider(key)
            if val is not None:
                with self._lock:
                    existing = self._secrets.get(key)
                    new_version = (existing.version + 1) if existing else 1
                    self._secrets[key] = Secret(
                        key=key, value=val, version=new_version, rotated_at=time.time()
                    )
                _LOGGER.info("Secret rotated: %s (v%d)", key, new_version)
                return True
        return False

    def rotate_all(self) -> int:
        count = 0
        with self._lock:
            keys = list(self._secrets.keys())
        for key in keys:
            if self.rotate(key):
                count += 1
        return count

    def start(self) -> None:
        self._stop_event.clear()
        self._bg_thread = threading.Thread(
            target=self._rotation_loop, daemon=True, name="secret-rotator"
        )
        self._bg_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._bg_thread and self._bg_thread.is_alive():
            self._bg_thread.join(timeout=5)

    def _rotation_loop(self) -> None:
        while not self._stop_event.is_set():
            self.rotate_all()
            self._stop_event.wait(self._rotation_interval)

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "total_secrets": len(self._secrets),
                "versions": {k: s.version for k, s in self._secrets.items()},
                "rotation_interval": self._rotation_interval,
            }


class EnvSecretProvider:
    def __call__(self, key: str) -> str | None:
        return os.getenv(key)

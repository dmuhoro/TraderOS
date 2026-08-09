from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests

from traderos.domain.ports import AuditPort
from traderos.domain.ports import MetricsPort
from traderos.domain.ports import SecretProviderPort
from traderos.infrastructure.resilience import VAULT_CB
from traderos.infrastructure.resilience import with_circuit_breaker

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
    """Cached secret holder with rotation and (optionally) access audit.

    ``audit`` records every read and rotation to the durable audit port so a
    secret's lifecycle is observable (G-04: secret-manager behaviour — live
    keys never leave the process, accesses and rotations are audited). Secret
    values are never written to the audit trail, only key names and versions.
    """

    def __init__(
        self,
        audit: AuditPort | None = None,
        metrics: MetricsPort | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._secrets: dict[str, Secret] = {}
        self._providers: list[SecretProviderPort] = []
        self._stop_event = threading.Event()
        self._bg_thread: threading.Thread | None = None
        self._rotation_interval = SECRET_ROTATION_INTERVAL
        self._audit = audit
        self._metrics = metrics

    def add_provider(self, provider: SecretProviderPort) -> None:
        self._providers.append(provider)

    def providers(self) -> list[SecretProviderPort]:
        """Snapshot of registered providers (read-only surface for probes)."""
        return list(self._providers)

    def get(self, key: str) -> str | None:
        with self._lock:
            secret = self._secrets.get(key)
            if secret is not None:
                self._audit_access(key, secret.version, "read.cached")
                return secret.value
        for provider in self._providers:
            val = provider(key)
            if val is not None:
                with self._lock:
                    self._secrets[key] = Secret(key=key, value=val, version=1)
                self._audit_access(key, 1, "read.provider")
                return val
        return None

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
                self._audit_rotate(key, new_version)
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

    def _audit_access(self, key: str, version: int, source: str) -> None:
        if self._audit:
            self._audit.record(
                "secret.accessed",
                "system",
                key,
                json.dumps({"source": source, "version": version, "value_redacted": True}),
            )
        if self._metrics:
            self._metrics.counter(f"secret.accessed.{source}", 1.0)

    def _audit_rotate(self, key: str, version: int) -> None:
        if self._audit:
            self._audit.record(
                "secret.rotated",
                "system",
                key,
                json.dumps({"version": version, "value_redacted": True}),
            )
        if self._metrics:
            self._metrics.counter("secret.rotated", 1.0)

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "total_secrets": len(self._secrets),
                "versions": {k: s.version for k, s in self._secrets.items()},
                "rotation_interval": self._rotation_interval,
            }


class EnvSecretProvider:
    """Environment-variable secret provider (local/paper default).

    Reads a raw environment variable for ``key``. This is the default provider
    for local/paper mode; live-mode deployments wire a real secret manager.
    """

    def get(self, key: str) -> str | None:
        return os.getenv(key)

    def __call__(self, key: str) -> str | None:
        return self.get(key)


class VaultFetchError(RuntimeError):
    """Vault itself is unreachable or misbehaving (network, timeout, corrupt body).

    Distinct from a missing/forbidden key, which is a normal data outcome and
    returns ``None``. Only genuine outages raise ; ``VAULT_CB`` counts them, so
    a Vault outage fails closed and loudly instead of silently demoting secret
    retrieval to a lower-trust provider (G-04 live-key boundary).
    """


class VaultSecretProvider:
    """Real HashiCorp Vault secret provider (KV v2) over the HTTP API.

    ``url`` is the Vault address (e.g. ``http://127.0.0.1:8200``), ``token`` a
    root/app-role token with read access, and ``mount`` the KV-v2 mount
    (default ``secret``). Secrets are read from ``<mount>/data/<key>`` —
    Vault's KV-v2 removed the ``value`` field to the nested ``data`` envelope.

    Fail-closed: a missing key (HTTP 4xx) returns ``None`` so the rotator can
    fall through to the next provider — a *missing secret* is not a Vault
    outage. A genuine outage (network error, timeout, HTTP 5xx, corrupt body)
    raises :class:`VaultFetchError`, which ``VAULT_CB`` counts and, once
    tripped, surfaces as ``CircuitOpenError`` fast-fail so callers never get a
    silently-degraded secret source.
    """

    def __init__(
        self,
        url: str,
        token: str,
        mount: str = "secret",
    ) -> None:
        self._url = url.rstrip("/")
        self._token = token
        self._mount = mount
        self._session = requests.Session()
        self._session.headers["X-Vault-Token"] = token
        self._session.headers["Accept"] = "application/json"

    def get(self, key: str) -> str | None:
        path = key if not key.startswith("/") else key[1:]
        url = f"{self._url}/v1/{self._mount}/data/{path}"
        body = self._fetch(url)
        if body is None:
            return None
        data = body.get("data", {}).get("data", {})
        value = data.get("value")
        return value if isinstance(value, str) else None

    @with_circuit_breaker(VAULT_CB)
    def _fetch(self, url: str) -> dict[str, Any] | None:
        """GET a KV-v2 secret; ``None`` on 4xx (missing/forbidden key)."""
        try:
            resp = self._session.get(url, timeout=5)
        except requests.RequestException as err:
            raise VaultFetchError(f"Vault unreachable fetching {url}") from err
        if resp.status_code >= 500:
            # 5xx is a genuine store outage: trip the breaker, fail closed.
            raise VaultFetchError(f"Vault returned HTTP {resp.status_code} for {url}")
        if resp.status_code != 200:
            # 4xx (missing/forbidden key) is a data outcome — not an outage.
            return None
        try:
            return resp.json()
        except ValueError as err:
            raise VaultFetchError(f"Vault returned a non-JSON 200 response for {url}") from err

    def __call__(self, key: str) -> str | None:
        return self.get(key)

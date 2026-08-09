"""WP1 — Secret Manager Integration (G-04 open item).

Proves, through the real code path:
1. A fetched secret value NEVER appears in the audit/log trail — only its
   access metadata (key, source, version, value_redacted: true) does.
2. EnvSecretProvider is the default when no real vault is configured, AND
   live-mode does NOT silently fall back to it when a real store is expected.
3. The real HashiCorp Vault KV-v2 provider retrieves values through the
   SecretProviderPort and fails closed (None) on missing keys / errors.
"""

from __future__ import annotations

import os
import sqlite3

import pytest
import requests

from traderos.infrastructure.database.migration_manager import migrate
from traderos.infrastructure.observability import SQLiteAuditService as Audit
from traderos.infrastructure.observability import SQLiteMetricsService as Metrics
from traderos.infrastructure.resilience import VAULT_CB
from traderos.infrastructure.secrets import EnvSecretProvider
from traderos.infrastructure.secrets import SecretRotator
from traderos.infrastructure.secrets import VaultFetchError
from traderos.infrastructure.secrets import VaultSecretProvider

VAULT_ADDR = "http://127.0.0.1:8200"
VAULT_TOKEN = "traderos-dev-root"
VAULT_PATH = "ALPACA_API_KEY"
VAULT_VALUE = "ALPACA_V3K_VAL"


def _vault_reachable() -> bool:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", 8200), timeout=1):
            return True
    except OSError:
        return False


requires_vault = pytest.mark.skipif(
    not _vault_reachable(),
    reason="No local HashiCorp Vault (docker run hashicorp/vault server -dev) available",
)


@pytest.fixture()
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate(conn)
    return conn


def _audit(db):
    return Audit(db)


def _vault_provider() -> VaultSecretProvider:
    return VaultSecretProvider(url=VAULT_ADDR, token=VAULT_TOKEN)


class TestProviderPortAbstractInterface:
    def test_env_provider_implements_port(self) -> None:
        assert hasattr(EnvSecretProvider(), "get")

    def test_vault_provider_implements_port(self) -> None:
        assert hasattr(_vault_provider(), "get")


class TestRedactionNeverLeaksValue:
    @requires_vault
    def test_fetched_value_absent_from_audit_and_metrics(self, db) -> None:
        audit, metrics = _audit(db), Metrics(db)
        rot = SecretRotator(audit=audit, metrics=metrics)
        rot.add_provider(_vault_provider())
        value = rot.get(VAULT_PATH)
        assert value == VAULT_VALUE, "provider must actually return the secret"

        entries = audit.get_entries(limit=20)
        assert entries, "access must be audited"
        trail = " ".join(f"{e.action}|{e.resource}|{e.detail}" for e in entries)
        assert "ALPACA_V3K_VAL" not in trail, "raw secret value leaked to audit"
        assert "value_redacted" in trail, "redaction flag recorded"
        assert "read" in trail, "access source recorded"

        metrics_blob = str(metrics.snapshot())
        assert "ALPACA_V3K_VAL" not in metrics_blob

    @requires_vault
    def test_rotation_records_access_without_value(self, db) -> None:
        audit = _audit(db)
        rot = SecretRotator(audit=audit)
        rot.add_provider(_vault_provider())
        assert rot.rotate(VAULT_PATH) is True
        blob = " ".join(e.detail for e in audit.get_entries(limit=500))
        assert "value_redacted" in blob
        assert "ALPACA_V3K_VAL" not in blob


class TestDefaultFallbackIsEnv:
    def test_env_provider_is_default_local_mode(self, monkeypatch) -> None:
        """No VAULT_ADDR -> EnvSecretProvider is registered (not Vault)."""
        from traderos.application import factory as f

        monkeypatch.delenv("VAULT_ADDR", raising=False)
        monkeypatch.delenv("VAULT_TOKEN", raising=False)
        rot = f._build_secret_rotator(audit=None, metrics=None)
        # The builder registers only the EnvSecretProvider fallback; smoke:
        monkeypatch.setenv("ALPACA_API_KEY", "env-fallback-value")
        assert rot.get("ALPACA_API_KEY") == "env-fallback-value"

    @requires_vault
    def test_live_boundary_resolves_vault_through_real_boot_helper(self, monkeypatch) -> None:
        """The exact helper the LIVE path calls (_build_secret_rotator) must
        resolve the real Vault key. This is the guardrail proof: the wiring the
        boot path uses, not a standalone service, retrieves from the secret
        manager. No env fallback — the key exists only in Vault."""
        from traderos.application import factory as f

        monkeypatch.setenv("VAULT_ADDR", VAULT_ADDR)
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        rot = f._build_secret_rotator(audit=None, metrics=None)
        assert rot.get("ALPACA_API_KEY") == VAULT_VALUE  # from Vault, not env

    def test_live_without_vault_boots_fail_closed_on_missing_key(self, monkeypatch) -> None:
        """LIVE key resolution aborts (not silently falls back) when the secret
        manager/env provides nothing — fail closed at the boot boundary."""
        from traderos.application import factory as f

        monkeypatch.delenv("VAULT_ADDR", raising=False)
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        rot = f._build_secret_rotator(audit=None, metrics=None)
        assert rot.get("ALPACA_API_KEY") is None

    @requires_vault
    def test_live_mode_does_not_silently_get_env_fallback(self, monkeypatch, db) -> None:
        """When a real secret manager is configured, retrieval goes through it,
        not a silent env fallback — proven by a key that exists only in Vault
        and not in env."""
        monkeypatch.setenv("VAULT_ADDR", VAULT_ADDR)
        rot = SecretRotator(audit=_audit(db))
        rot.add_provider(_vault_provider())
        # The env var is NOT set; value comes only from Vault.
        monkeypatch.delenv(VAULT_PATH, raising=False)
        assert rot.get(VAULT_PATH) == VAULT_VALUE  # from Vault, not env

    @requires_vault
    def test_vault_value_never_hits_plain_env(self, monkeypatch) -> None:
        """The Vault value is NOT stored in the process env (no accidental
        injection of the secret into an observable env surface)."""
        from traderos.application import factory as f

        monkeypatch.setenv("VAULT_ADDR", VAULT_ADDR)
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        rot = f._build_secret_rotator(audit=None, metrics=None)
        assert rot.get("ALPACA_API_KEY") == VAULT_VALUE
        # Retrieval must NOT inject the secret into the process env surface.
        assert os.getenv("ALPACA_API_KEY") is None

    @requires_vault
    def test_missing_key_fails_closed_returns_none(self) -> None:
        assert _vault_provider().get("no/such/key") is None

    def test_no_silent_env_fallback_when_provider_configured(self, monkeypatch, db) -> None:
        """Fail-closed: when a provider is registered and it does NOT hold the
        key, the rotator must return None even if the same key exists in the
        process env. Env access flows only through EnvSecretProvider, never via
        a hidden bypass in the rotator, so a live deployment cannot silently
        fall back to env vars behind the secret manager's back."""
        rot = SecretRotator(audit=_audit(db))
        rot.add_provider(lambda _key: None)  # provider that answers nothing
        monkeypatch.setenv("ALPACA_API_KEY", "env-key-must-not-leak")
        assert rot.get("ALPACA_API_KEY") is None

        # Explicit env provider remains an opt-in registered provider.
        rot2 = SecretRotator(audit=_audit(db))
        rot2.add_provider(EnvSecretProvider())
        assert rot2.get("ALPACA_API_KEY") == "env-key-must-not-leak"


class TestVaultDecodeAndCall:
    """Decoder branches and __call__ delegation, driven by a stubbed transport
    so the outage paths are exercised without a live Vault."""

    @pytest.fixture(autouse=True)
    def _reset_vault_breaker(self) -> None:
        VAULT_CB.reset()
        yield
        VAULT_CB.reset()

    def _stub_provider(self, response: requests.Response) -> VaultSecretProvider:
        provider = VaultSecretProvider(url=VAULT_ADDR, token=VAULT_TOKEN)

        def get(url, timeout=None):  # matching requests.Session.get signature
            return response

        provider._session.get = get
        return provider

    def test_non_json_200_body_is_an_outage(self) -> None:
        """A 200 that is not JSON is not a secret — it is a corrupt store and
        must surface as a VaultFetchError (VaultFetchError.secrets.py dip 1)."""
        resp = requests.Response()
        resp.status_code = 200
        resp._content = b"<html>not-json</html>"
        with pytest.raises(VaultFetchError):
            self._stub_provider(resp).get("api/key")

    def test_5xx_body_is_an_outage(self) -> None:
        resp = requests.Response()
        resp.status_code = 500
        with pytest.raises(VaultFetchError):
            self._stub_provider(resp).get("api/key")

    def test_non_string_value_returns_none(self) -> None:
        resp = requests.Response()
        resp.status_code = 200
        resp._content = b'{"data":{"data":{"value":123}}}'
        assert self._stub_provider(resp).get("api/key") is None

    def test_call_delegates_to_get(self, monkeypatch) -> None:
        provider = VaultSecretProvider(url=VAULT_ADDR, token=VAULT_TOKEN)
        monkeypatch.setattr(provider, "get", lambda key: "stubbed-value")
        assert provider("api/key") == "stubbed-value"

    def test_missing_key_returns_none_through_stubbed_transport(self) -> None:
        resp = requests.Response()
        resp.status_code = 404
        # A 4xx is a data outcome, not an outage: the decoder must return None.
        assert self._stub_provider(resp).get("api/missing-key") is None

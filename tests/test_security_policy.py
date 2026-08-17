from __future__ import annotations

import pytest

from traderos.infrastructure.auth import APIKeyAuthenticator
from traderos.infrastructure.security_policy import SecurityPolicyError
from traderos.infrastructure.security_policy import assert_production_policy
from traderos.infrastructure.security_policy import check_security_posture
from traderos.infrastructure.security_policy import deployment_environment


def _authed() -> APIKeyAuthenticator:
    return APIKeyAuthenticator(
        admin_keys=("admin-secret-key",),
        operator_keys=("operator-secret-key",),
    )


class TestDeploymentEnvironment:
    def test_defaults_to_development(self, monkeypatch) -> None:
        monkeypatch.delenv("TRADEROS_ENV", raising=False)
        assert deployment_environment() == "development"

    def test_normalizes_case_and_whitespace(self, monkeypatch) -> None:
        monkeypatch.setenv("TRADEROS_ENV", "  Production ")
        assert deployment_environment() == "production"


class TestCheckSecurityPosture:
    def test_development_is_always_secure(self, monkeypatch) -> None:
        monkeypatch.delenv("TRADEROS_ENV", raising=False)
        report = check_security_posture(authenticator=APIKeyAuthenticator())
        assert report.all_ok is True
        assert report.environment == "development"

    def test_production_flags_open_auth(self, monkeypatch) -> None:
        monkeypatch.setenv("TRADEROS_ENV", "production")
        report = check_security_posture(authenticator=APIKeyAuthenticator())
        assert report.all_ok is False
        assert any(f.check == "auth" and not f.ok for f in report.findings)

    def test_production_flags_missing_tls(self, monkeypatch) -> None:
        monkeypatch.setenv("TRADEROS_ENV", "production")
        report = check_security_posture(
            authenticator=_authed(),
            ssl_keyfile=None,
            ssl_certfile=None,
        )
        assert any(f.check == "tls" and not f.ok for f in report.findings)

    def test_production_tls_passes_when_proxy_flagged(self, monkeypatch) -> None:
        monkeypatch.setenv("TRADEROS_ENV", "production")
        report = check_security_posture(
            authenticator=_authed(),
            ssl_keyfile=None,
            ssl_certfile=None,
            tls_terminated_by_proxy="true",
        )
        assert all(f.ok for f in report.findings if f.check == "tls")
        tls = next(f for f in report.findings if f.check == "tls")
        assert "trusted platform edge" in tls.detail

    def test_production_tls_reads_proxy_flag_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("TRADEROS_ENV", "production")
        monkeypatch.setenv("TLS_TERMINATED_BY_PROXY", "true")
        report = check_security_posture(authenticator=_authed())
        assert all(f.ok for f in report.findings if f.check == "tls")

    def test_production_flags_cors_allow_all(self, monkeypatch) -> None:
        monkeypatch.setenv("TRADEROS_ENV", "production")
        report = check_security_posture(authenticator=_authed(), cors_origins="*")
        assert any(f.check == "cors" and not f.ok for f in report.findings)

    def test_production_secure_when_hardened(self, monkeypatch) -> None:
        monkeypatch.setenv("TRADEROS_ENV", "production")
        report = check_security_posture(
            authenticator=_authed(),
            ssl_keyfile="/keys/server.key",
            ssl_certfile="/keys/server.crt",
            cors_origins="https://app.example.com",
        )
        assert report.all_ok is True

    def test_cors_specific_origins_allowed(self, monkeypatch) -> None:
        monkeypatch.setenv("TRADEROS_ENV", "production")
        report = check_security_posture(
            authenticator=_authed(),
            cors_origins="https://a.example.com,https://b.example.com",
        )
        assert all(f.ok for f in report.findings if f.check == "cors")

    def test_rotation_interval_zero_is_a_finding(self, monkeypatch) -> None:
        monkeypatch.setenv("TRADEROS_ENV", "production")
        report = check_security_posture(
            authenticator=_authed(),
            rotation_interval=0,
        )
        assert any(f.check == "secret_rotation" and not f.ok for f in report.findings)

    def test_to_dict_shape(self, monkeypatch) -> None:
        monkeypatch.setenv("TRADEROS_ENV", "production")
        report = check_security_posture(authenticator=APIKeyAuthenticator())
        data = report.to_dict()
        assert data["environment"] == "production"
        assert data["verdict"] == "INSUFFICIENT"
        assert isinstance(data["findings"], list)


class TestAssertProductionPolicy:
    def test_noop_in_development(self, monkeypatch) -> None:
        monkeypatch.delenv("TRADEROS_ENV", raising=False)
        assert_production_policy(authenticator=APIKeyAuthenticator())
        assert_production_policy(
            authenticator=APIKeyAuthenticator(), ssl_keyfile=None, ssl_certfile=None
        )

    def test_raises_when_auth_open_in_production(self, monkeypatch) -> None:
        monkeypatch.setenv("TRADEROS_ENV", "production")
        with pytest.raises(SecurityPolicyError):
            assert_production_policy(authenticator=APIKeyAuthenticator())

    def test_raises_when_tls_missing_in_production(self, monkeypatch) -> None:
        monkeypatch.setenv("TRADEROS_ENV", "production")
        with pytest.raises(SecurityPolicyError):
            assert_production_policy(authenticator=_authed(), ssl_keyfile=None, ssl_certfile=None)

    def test_passes_when_hardened(self, monkeypatch) -> None:
        monkeypatch.setenv("TRADEROS_ENV", "production")
        assert_production_policy(
            authenticator=_authed(),
            ssl_keyfile="/keys/server.key",
            ssl_certfile="/keys/server.crt",
        )

    def test_passes_when_tls_terminated_by_proxy(self, monkeypatch) -> None:
        monkeypatch.setenv("TRADEROS_ENV", "production")
        assert_production_policy(
            authenticator=_authed(),
            ssl_keyfile=None,
            ssl_certfile=None,
            tls_terminated_by_proxy="true",
        )

    def test_accepts_prebuilt_report(self, monkeypatch) -> None:
        monkeypatch.setenv("TRADEROS_ENV", "production")
        report = check_security_posture(
            authenticator=_authed(),
            ssl_keyfile="/keys/server.key",
            ssl_certfile="/keys/server.crt",
        )
        assert_production_policy(report=report)

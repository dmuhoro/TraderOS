from __future__ import annotations

import os
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from traderos.domain.exceptions import ConfigError
from traderos.infrastructure.auth import APIKeyAuthenticator
from traderos.infrastructure.secrets import SECRET_ROTATION_INTERVAL

PRODUCTION_ENV = "production"
ENV_VAR = "TRADEROS_ENV"


class SecurityPolicyError(ConfigError):
    """Raised when the deployment security posture is insufficient.

    Used to fail closed in production: the API refuses to serve until the
    operator has configured API keys and TLS. Development and CI stay
    frictionless because the guard only engages for ``TRADEROS_ENV=production``.
    """


def deployment_environment() -> str:
    value = os.getenv(ENV_VAR, "").strip().lower()
    return value or "development"


@dataclass(frozen=True)
class SecurityFinding:
    check: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"check": self.check, "ok": self.ok, "detail": self.detail}


@dataclass
class SecurityReport:
    environment: str
    findings: list[SecurityFinding] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all(finding.ok for finding in self.findings)

    @property
    def fails(self) -> list[SecurityFinding]:
        return [finding for finding in self.findings if not finding.ok]

    def to_dict(self) -> dict[str, Any]:
        return {
            "environment": self.environment,
            "verdict": "SECURE" if self.all_ok else "INSUFFICIENT",
            "findings": [finding.to_dict() for finding in self.findings],
        }


def _tls_configured(ssl_keyfile: str | None, ssl_certfile: str | None) -> bool:
    return bool(ssl_keyfile and ssl_certfile)


def _cors_allow_all(cors_origins: str | None) -> bool:
    origins = (cors_origins if cors_origins is not None else os.getenv("CORS_ORIGINS", "")).strip()
    return origins == "*"


def check_security_posture(
    authenticator: APIKeyAuthenticator | None = None,
    ssl_keyfile: str | None = None,
    ssl_certfile: str | None = None,
    cors_origins: str | None = None,
    rotation_interval: int | None = None,
) -> SecurityReport:
    """Assess the deployment posture against the policy for the active environment.

    Production requires authentication and TLS and forbids CORS allow-all.
    Development/CI are open by default (matching ``APIKeyAuthenticator``), so
    the checks pass with informational detail while local work stays unblocked.
    """
    env = deployment_environment()
    auth = authenticator if authenticator is not None else APIKeyAuthenticator.from_env()
    report = SecurityReport(environment=env)

    tls_ok = _tls_configured(ssl_keyfile, ssl_certfile)
    cors_ok = not _cors_allow_all(cors_origins)
    interval = rotation_interval if rotation_interval is not None else SECRET_ROTATION_INTERVAL

    if env == PRODUCTION_ENV:
        report.findings.append(
            SecurityFinding(
                "auth",
                auth.enabled,
                "API keys configured" if auth.enabled else "API authentication disabled (open)",
            )
        )
        report.findings.append(
            SecurityFinding(
                "tls",
                tls_ok,
                "TLS configured" if tls_ok else "TLS not configured (plaintext HTTP)",
            )
        )
        report.findings.append(
            SecurityFinding(
                "cors",
                cors_ok,
                "CORS restricts origins" if cors_ok else "CORS allow-all '*' is forbidden",
            )
        )
    else:
        report.findings.append(
            SecurityFinding(
                "auth",
                True,
                "auth enabled" if auth.enabled else "auth open (development default)",
            )
        )
        report.findings.append(
            SecurityFinding(
                "tls",
                True,
                "TLS configured" if tls_ok else "TLS optional (development default)",
            )
        )
        report.findings.append(
            SecurityFinding(
                "cors",
                cors_ok,
                "CORS restricts origins" if cors_ok else "CORS allow-all '*' (development default)",
            )
        )

    report.findings.append(
        SecurityFinding(
            "secret_rotation",
            interval > 0,
            f"rotation interval {interval}s",
        )
    )
    return report


def assert_production_policy(report: SecurityReport | None = None, **kwargs: Any) -> None:
    """Fail closed in production when any security check fails.

    No-op in any other environment so local development and CI remain
    frictionless. In production a violation raises ``SecurityPolicyError``
    before the server starts listening.
    """
    if deployment_environment() != PRODUCTION_ENV:
        return
    report = report if report is not None else check_security_posture(**kwargs)
    fails = report.fails
    if fails:
        summary = "; ".join(f"{f.check}: {f.detail}" for f in fails)
        raise SecurityPolicyError(f"Production security policy violation: {summary}")

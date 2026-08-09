from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from enum import Enum

ADMIN_ENV = "TRADEROS_ADMIN_API_KEY"
OPERATOR_ENV = "TRADEROS_OPERATOR_API_KEY"
VIEWER_ENV = "TRADEROS_VIEWER_API_KEY"
LEGACY_ADMIN_ENV = "TRADEROS_API_KEY"


class Role(Enum):
    """Least-privilege operator roles exposed to the REST surface."""

    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


class Permission(Enum):
    """Capability buckets a role is granted. Roles are hierarchical."""

    READ = "read"
    OPERATE = "operate"
    ADMIN = "admin"


_ROLE_RANK: dict[Role, int] = {
    Role.VIEWER: 0,
    Role.OPERATOR: 1,
    Role.ADMIN: 2,
}

_PERMISSION_RANK: dict[Permission, int] = {
    Permission.READ: 0,
    Permission.OPERATE: 1,
    Permission.ADMIN: 2,
}


def _split_keys(value: str) -> tuple[str, ...]:
    return tuple(
        part.strip() for part in value.split(",") if part.strip() and len(part.strip()) >= 8
    )


def role_grants(role: Role | None, permission: Permission) -> Role | None:
    """Return ``role`` if it holds ``permission`` (hierarchical), else None.

    This is the pure role-check used by session-auth: the RBAC permission
    lattice is applied to an already-verified identity, whether that identity
    came from an API key or a server-issued session token.
    """
    if role is None:
        return None
    if _ROLE_RANK[role] >= _PERMISSION_RANK[permission]:
        return role
    return None


@dataclass(frozen=True)
class APIKeyAuthenticator:
    """Constant-time API-key authenticator with role resolution.

    When no keys are configured authentication is disabled (the API is open)
    so local development and CI remain frictionless. The moment any key is
    configured every protected route enforces it. Key comparison is constant
    time (``hmac.compare_digest``) so timing attacks cannot recover keys.
    """

    admin_keys: tuple[str, ...] = ()
    operator_keys: tuple[str, ...] = ()
    viewer_keys: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> APIKeyAuthenticator:
        legacy = os.getenv(LEGACY_ADMIN_ENV, "")
        admin = os.getenv(ADMIN_ENV, "")
        admin_raw = legacy if not admin else admin
        return cls(
            admin_keys=_split_keys(admin_raw),
            operator_keys=_split_keys(os.getenv(OPERATOR_ENV, "")),
            viewer_keys=_split_keys(os.getenv(VIEWER_ENV, "")),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.admin_keys or self.operator_keys or self.viewer_keys)

    @property
    def configured_roles(self) -> dict[str, list[str]]:
        return {
            Role.ADMIN.value: list(self.admin_keys),
            Role.OPERATOR.value: list(self.operator_keys),
            Role.VIEWER.value: list(self.viewer_keys),
        }

    def role_for_key(self, key: str | None) -> Role | None:
        if not key:
            return None
        for role, keys in (
            (Role.ADMIN, self.admin_keys),
            (Role.OPERATOR, self.operator_keys),
            (Role.VIEWER, self.viewer_keys),
        ):
            for candidate in keys:
                if hmac.compare_digest(key, candidate):
                    return role
        return None

    def authorize(self, key: str | None, permission: Permission) -> Role | None:
        """Return the authenticated role if it holds ``permission``, else None.

        Open (no keys configured) authorizes every caller. When configured,
        a missing/invalid key yields None for every permission.
        """
        if not self.enabled:
            return Role.ADMIN
        role = self.role_for_key(key)
        if role is None:
            return None
        required = _PERMISSION_RANK[permission]
        if _ROLE_RANK[role] >= required:
            return role
        return None

    def describe(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "roles": {
                role: len(keys)
                for role, keys in (
                    (Role.VIEWER.value, self.viewer_keys),
                    (Role.OPERATOR.value, self.operator_keys),
                    (Role.ADMIN.value, self.admin_keys),
                )
            },
        }

# pyright: reportUntypedFunctionDecorator=false, reportUnusedFunction=false, reportOptionalCall=false, reportPrivateUsage=false, reportUntypedBaseClass=false

"""FastAPI RBAC wiring for the operator surface.

Routes opt in via ``dependencies=[Depends(require_read)]`` etc. The policy is:
authentication is *open* until at least one API key is configured; after that
every protected route demands a valid ``X-API-Key`` and the role required by
the route's permission bucket (401 invalid, 403 insufficient role).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request

from traderos.infrastructure.auth import APIKeyAuthenticator
from traderos.infrastructure.auth import Permission
from traderos.infrastructure.auth import Role

_authenticator: APIKeyAuthenticator | None = None


def get_authenticator() -> APIKeyAuthenticator:
    global _authenticator
    if _authenticator is None:
        _authenticator = APIKeyAuthenticator.from_env()
    return _authenticator


def reset_authenticator() -> None:
    global _authenticator
    _authenticator = None


def set_authenticator(auth: APIKeyAuthenticator) -> None:
    global _authenticator
    _authenticator = auth


def _header_key(request: Request) -> str | None:
    return request.headers.get("X-API-Key")


def current_role(request: Request) -> Role | None:
    """Resolve the caller's role; 401 on an invalid key when auth is enabled."""
    auth = get_authenticator()
    if not auth.enabled:
        return None
    role = auth.role_for_key(_header_key(request))
    if role is None:
        raise HTTPException(401, "Unauthorized: invalid or missing API key")
    return role


def _permission_dependency(permission: Permission):
    def _dependency(
        request: Request,
        role: Annotated[Role | None, Depends(current_role)] = None,
    ) -> Role | None:
        auth = get_authenticator()
        if not auth.enabled:
            return None
        granted = auth.authorize(_header_key(request), permission)
        if granted is None:
            raise HTTPException(403, "Forbidden: insufficient permissions for this action")
        return role

    return _dependency


require_read = _permission_dependency(Permission.READ)
require_operate = _permission_dependency(Permission.OPERATE)
require_admin = _permission_dependency(Permission.ADMIN)


def auth_info(request: Request) -> dict[str, object]:
    """Self-describing auth state used by the login screen and ops tooling."""
    auth = get_authenticator()
    role = auth.role_for_key(_header_key(request)) if auth.enabled else None
    return {
        "authenticated": role is not None,
        "required": auth.enabled,
        "role": role.value if role is not None else None,
        "roles": auth.describe()["roles"],
    }

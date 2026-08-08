# pyright: reportUntypedFunctionDecorator=false, reportUnusedFunction=false, reportOptionalCall=false, reportPrivateUsage=false, reportUntypedBaseClass=false

"""FastAPI RBAC wiring for the operator surface.

Routes opt in via ``dependencies=[Depends(require_read)]`` etc. The policy is:
authentication is *open* until at least one API key is configured; after that
every protected route demands a valid ``X-API-Key`` and the role required by
the route's permission bucket (401 invalid, 403 insufficient role).
"""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request

from traderos.infrastructure.auth import APIKeyAuthenticator
from traderos.infrastructure.auth import Permission
from traderos.infrastructure.auth import Role
from traderos.interfaces.api import sse_tokens

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


def require_sse(request: Request) -> None:
    """Authenticate the browser SSE feed.

    ``EventSource`` cannot attach the ``X-API-Key`` header, so the dashboard
    mints a short-lived single-purpose token (see ``sse_tokens``) through an
    authenticated endpoint and passes it as ``?token=...``.

    Two disjoint credentials, both fail-closed:

    * ``?token=`` present -> the token is the credential. Invalid, expired or
      replayed -> explicit 401; it can never silently fall back.
    * No token -> the ordinary header seam applies unchanged.
    """
    token = request.query_params.get("token")
    if token is not None:
        if not sse_tokens.validate(token):
            raise HTTPException(401, "Unauthorized: invalid, expired or reused event token")
        return
    if not get_authenticator().enabled:
        return
    role = get_authenticator().role_for_key(_header_key(request))
    if role is None:
        raise HTTPException(401, "Unauthorized: invalid or missing API key")
    granted = get_authenticator().authorize(_header_key(request), Permission.READ)
    if granted is None:
        raise HTTPException(403, "Forbidden: insufficient permissions for this action")


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


# ---------------------------------------------------------------------------
# Boundary-enforced authentication (A1).
#
# The per-route ``Depends(require_*)`` checks are a refinement, not the first
# line of defense: a route added without a dependency is otherwise silently
# open when keys are configured. This guard enforces a single fail-closed
# boundary on the API so protection no longer depends on each developer
# remembering to add a dependency.
#
# Fail-closed rule: *whenever* authentication is required (any key configured,
# or a live trading posture), every request to a non-public path MUST present a
# valid key. Public paths are an explicit, small allow-list for liveness probes
# and the self-describing auth endpoint — never for risk/operate surfaces.
# ---------------------------------------------------------------------------


PUBLIC_PATH_PREFIXES: tuple[str, ...] = (
    "/v1/healthz",
    "/v1/auth/me",
    "/v1/health",
    # The retail seam authenticates with SESSIONS (not API keys). Its own
    # endpoints are still fail-closed: every retail handler depends on
    # require_user() which 401s without a valid session token. The API-key
    # boundary deliberately does not apply to it.
    "/v1/retail",
)

# The auth boundary guards the operator/risk surface only: every request under
# /v1/* (except the public prefixes) must be authenticated. Static assets,
# Prometheus /metrics, the dashboard bundle and OpenAPI docs live outside the
# /v1 seam and are served as read-only without a boundary challenge.
V1_PREFIX = "/v1"


def auth_required() -> bool:
    """True when the API must authenticate clients before serving.

    Auth is required when any key is configured, or when a live trading mode
    is declared (fail-closed: live posture can never be served anonymously,
    even if the operator forgot to set a key).
    """
    auth = get_authenticator()
    if auth.enabled:
        return True
    mode = os.getenv("TRADING_MODE", "").strip().lower()
    return mode in ("live", "paper")


def _public_path(path: str) -> bool:
    for prefix in PUBLIC_PATH_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def _within_boundary(path: str) -> bool:
    return path.startswith(V1_PREFIX)


def enforce_auth_boundary(request: Request) -> None:
    """Fail-closed boundary guard: raise 401 unless a non-public request is
    authenticated with a valid key.

    Run at the HTTP seam (outside route dispatch) so a route lacking a
    ``Depends(require_*)`` is still denied. Public probes stay reachable for
    the health checks and the auth-info endpoint.
    """
    if not auth_required():
        return
    if not _within_boundary(request.url.path):
        return
    if _public_path(request.url.path):
        return
    if request.url.path == "/v1/events" and sse_tokens.peek(request.query_params.get("token")):
        # The browser SSE feed authenticates with a short-lived single-purpose
        # token (EventSource cannot set X-API-Key). The boundary lets a
        # well-formed unexpired token through; the route's require_sse check
        # then performs the real consuming single-use validation, so a replayed
        # token is still rejected end-to-end.
        return
    auth = get_authenticator()
    role = auth.role_for_key(_header_key(request))
    if role is None:
        raise HTTPException(401, "Unauthorized: a valid API key is required")

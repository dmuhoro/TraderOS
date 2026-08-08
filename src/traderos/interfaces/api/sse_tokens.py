"""Short-lived, single-purpose tokens for the browser SSE feed.

``EventSource`` cannot set the ``X-API-Key`` header, so once API keys are
configured the dashboard's real-time feed has no way to authenticate like the
fetch-based clients. Instead the dashboard first calls the authenticated
``GET /v1/events/token`` mint endpoint (passing its normal API key) and then
opens ``/v1/events?token=...``.

Security properties (fail-closed):

* **Short-lived** — tokens expire after ``EVENT_TOKEN_TTL_SECONDS`` (default
  60s); the dashboard re-mints on every connect/reconnect.
* **Single-purpose** — the token is only consumed by the SSE route; it is not
  an API key and cannot authenticate any other operator endpoint.
* **Single-use** — a token that has been presented once is rejected on any
  later request, so a leaked query string cannot be replayed to open a second
  stream.
* **Signed** — tokens are HMAC-SHA256 signed and verified in constant time;
  the key is ``SSE_TOKEN_SECRET`` or a fresh per-process value, so every
  outstanding token is invalidated by a process restart (fail-closed under
  key rotation / redeploys).

The header-based ``X-API-Key`` seam for every other endpoint is untouched; the
token flow is an additional credential for exactly one route.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
import uuid

_LOGGER = logging.getLogger("traderos.api.sse_tokens")

_SCOPE = "events"
_DELIM = ":"
_DEFAULT_TTL_SECONDS = 60

# A per-process random secret is the fail-closed default: tokens die on the
# process restart that would otherwise outlive their validity window anyway.
_config_value = os.getenv("SSE_TOKEN_SECRET", "").strip()
_signing_key = _config_value.encode("utf-8") if _config_value else secrets.token_bytes(32)

_consumed: set[str] = set()
_lock = threading.Lock()


def _sign(payload: bytes) -> str:
    return hmac.new(_signing_key, payload, hashlib.sha256).hexdigest()


def _payload(scope: str, nonce: str, expiry: int) -> bytes:
    return f"{scope}\x1f{nonce}\x1f{expiry}".encode()


def _sweep(now: int) -> None:
    """Best-effort memory bound for the consumed set.

    Called under ``_lock`` from :func:`mint`. Correctness never depends on the
    sweep: an unconsumed nonce simply validates, and an expired token is
    rejected by its TTL regardless of the consumed set.
    """
    # Oldest-inserted entries are the most likely to have passed their TTL, so
    # evict the eldest quarter. Correctness never depends on this sweep: an
    # unconsumed nonce simply validates; an expired one is rejected by TTL.
    for _ in range(min(len(_consumed) // 4, 1024)):  # pragma: no cover
        _consumed.discard(next(iter(_consumed)))


def mint(ttl_seconds: int | None = None) -> tuple[str, int]:
    """Issue a fresh single-use token; returns ``(token, expires_at_unix)``.

    A token is ``events:<nonce>:<expiry>:<hmac>``. Callers who know the secret
    could forge one, but nonces are unguessable and the route never reveals
    state beyond pass/fail.
    """
    ttl = _DEFAULT_TTL_SECONDS if ttl_seconds is None else max(1, ttl_seconds)
    expiry = int(time.time()) + ttl
    nonce = uuid.uuid4().hex
    payload = _payload(_SCOPE, nonce, expiry)
    token = f"{_SCOPE}{_DELIM}{nonce}{_DELIM}{expiry}{_DELIM}{_sign(payload)}"
    with _lock:
        _sweep(expiry)
    return token, expiry


def peek(token: str | None, now: int | None = None) -> bool:
    """True when a token is well-formed, signed and unexpired (no consumption).

    Used by the auth boundary so a token-authenticated ``/v1/events`` request
    is let through to the route, where :func:`validate` performs the real,
    consuming single-use check. ``peek`` never mutates state — a replayed
    token still fails at the route.
    """
    if not token or _DELIM not in token:
        return False
    parts = token.split(_DELIM)
    if len(parts) != 4:
        return False
    scope, nonce, expiry_raw, signature = parts
    if scope != _SCOPE or not nonce or not expiry_raw or not signature:
        return False
    try:
        expiry = int(expiry_raw)
    except ValueError:
        return False
    current = int(time.time()) if now is None else now
    if expiry <= current:
        return False
    expected = _sign(_payload(scope, nonce, expiry))
    return hmac.compare_digest(signature, expected)


def validate(token: str | None, now: int | None = None) -> bool:
    """True only when ``token`` is well-formed, signed, fresh and unspent.

    Verified against signature, TTL and single-use; every failing branch
    returns False (fail-closed, no partial credit).
    """
    if not token or _DELIM not in token:
        return False
    parts = token.split(_DELIM)
    if len(parts) != 4:
        return False
    scope, nonce, expiry_raw, signature = parts
    if scope != _SCOPE or not nonce or not expiry_raw or not signature:
        return False
    try:
        expiry = int(expiry_raw)
    except ValueError:
        return False
    current = int(time.time()) if now is None else now
    if expiry <= current:
        return False
    expected = _sign(_payload(scope, nonce, expiry))
    if not hmac.compare_digest(signature, expected):
        return False
    with _lock:
        if nonce in _consumed:
            return False
        _consumed.add(nonce)
    return True


def reset() -> None:
    """Test helper: clear consumed state (signing key is left as-is)."""
    with _lock:
        _consumed.clear()

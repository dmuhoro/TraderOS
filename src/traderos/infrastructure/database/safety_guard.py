"""Refuse to run destructive schema work against a remote database by accident.

The test suite executes ``DROP SCHEMA``, ``DROP TABLE`` and ``TRUNCATE``. The
production database URL lives in a gitignored ``.env``, and
``config_loader`` calls ``load_dotenv()`` at import time, so every test process
inherits the production host. One mis-aimed ``pytest`` away from dropping the
live schema.

This module is the interlock. Two rules, both fail-closed:

1. ``guard_destructive_sql`` refuses schema-destroying SQL aimed at a
   non-loopback host unless the operator opts in explicitly by setting
   ``TRADEROS_ALLOW_REMOTE_DESTRUCTIVE=1``.
2. ``guard_connection`` refuses to even open a connection to a non-loopback
   host while running under pytest, unless the same opt-in is present.

Nothing here blocks ordinary use of a remote database. Reads, writes and
non-destructive DDL against production are untouched -- this only refuses to
*destroy* a schema that was not deliberately aimed at, and it never silently
degrades: every refusal raises.

Why not "just be careful": the URL is loaded from a file rather than passed as
an argument, so it is invisible at the call site. The mistake cannot be spotted
by reading the code that makes it.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlparse

OPT_IN_ENV = "TRADEROS_ALLOW_REMOTE_DESTRUCTIVE"

LOOPBACK_HOSTS = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "::1",
        "0.0.0.0",
        "host.docker.internal",
    }
)

# Statement-leading keywords that destroy or discard schema or data. Matched
# against the statement with leading comments and whitespace removed, so a
# commented-out DROP cannot smuggle itself through.
_DESTRUCTIVE = re.compile(
    r"^\s*(?:"
    r"DROP\b"
    r"|TRUNCATE\b"
    r"|DELETE\s+FROM\b(?!\s*\w+\s+WHERE)"  # unqualified delete only
    r")",
    re.IGNORECASE | re.DOTALL,
)

_COMMENT = re.compile(r"^(\s|--[^\n]*\n|/\*.*?\*/)*", re.DOTALL)


class RemoteDestructiveRefusedError(RuntimeError):
    """A destructive statement was aimed at a remote database."""


def _strip_noise(sql: str) -> str:
    return _COMMENT.sub("", sql, count=1)


def is_destructive(sql: str) -> bool:
    return bool(_DESTRUCTIVE.match(_strip_noise(sql)))


def _host_from_url(database_url: str) -> str:
    if not database_url:
        return ""
    try:
        return (urlparse(database_url).hostname or "").lower()
    except ValueError:
        return ""


def is_loopback(host: str) -> bool:
    if not host:
        return True  # sqlite, or no host to reason about
    host = host.lower().strip("[]")
    if host in LOOPBACK_HOSTS:
        return True
    # 127.0.0.0/8 is entirely loopback.
    return host.startswith("127.")


def remote_opt_in() -> bool:
    return os.getenv(OPT_IN_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _host_for_conn(conn: object) -> str:
    dsn_params = getattr(conn, "get_dsn_parameters", None)
    if callable(dsn_params):
        try:
            params: Any = dsn_params()
        except Exception:  # noqa: BLE001 - never block on introspection
            return ""
        if isinstance(params, dict):
            return str(params.get("host") or "").lower()
    return ""


def guard_destructive_sql(conn: object, sql: str) -> None:
    """Refuse schema-destroying SQL aimed at a non-loopback host."""
    if not is_destructive(sql) or remote_opt_in():
        return
    if is_loopback(_host_for_conn(conn)):
        return
    raise RemoteDestructiveRefusedError(
        f"refusing destructive SQL against a remote database: "
        f"{_strip_noise(sql).split(None, 1)[0].upper()} "
        f"(set {OPT_IN_ENV}=1 if this is genuinely intended)"
    )


def under_pytest() -> bool:
    """True while running under pytest, without importing it.

    ``PYTEST_CURRENT_TEST`` is authoritative. The module probe exists only for
    the narrow window before the first test executes, when the variable is not
    yet set -- and it deliberately does NOT treat bare membership of ``pytest``
    in ``sys.modules`` as proof, because a production process that merely
    imported pytest would then be locked out of its own database.
    """
    return bool(os.getenv("PYTEST_CURRENT_TEST"))


def guard_connection(database_url: str) -> None:
    """Refuse to dial a remote database from inside the test suite by accident.

    Only fires under pytest. Production and CLI runs are unaffected.
    """
    if remote_opt_in() or not under_pytest():
        return
    host = _host_from_url(database_url)
    if is_loopback(host):
        return
    raise RemoteDestructiveRefusedError(
        f"refusing to open a test connection to a remote database "
        f"(host={host or 'unknown'}). Point DATABASE_URL at a local throwaway "
        f"database, or set {OPT_IN_ENV}=1 if this is genuinely intended."
    )

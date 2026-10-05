"""Regression proof: the safety guard must never fail OPEN on a real DSN.

Both tests here exist because the guard shipped with a defect that made it
useless against the actual production URL. ``urlparse`` raises ``ValueError``
when the netloc contains a bracketed host it cannot interpret, which a password
containing an unescaped ``@`` triggers. That exception was caught and collapsed
to ``""``, and ``is_loopback("")`` is True -- so an unparseable DSN was treated
as local and the interlock stood down. The suite then dialed production.

The failure was invisible because no test exercised a URL shaped like the real
one. These are that test.
"""

from __future__ import annotations

import pytest

from traderos.infrastructure.database.safety_guard import RemoteDestructiveRefusedError
from traderos.infrastructure.database.safety_guard import guard_connection
from traderos.infrastructure.database.safety_guard import host_from_url
from traderos.infrastructure.database.safety_guard import is_loopback


class TestHostExtraction:
    def test_password_with_unescaped_at_sign_still_resolves_the_host(self) -> None:
        """The real production DSN shape, confirmed against the actual ``.env``.

        Its password contains ``[`` and ``]``, so ``urlparse`` treats the userinfo
        as an IPv6 literal, fails to parse it, and raises. That exception used to
        be swallowed into an empty host, which then read as loopback. The host
        must still be recovered, because "could not parse" must never mean
        "treated as local".
        """
        url = "postgresql://user:p@ssw0rd@db.weoymywxwmiudephpxet.supabase.co:5432/postgres"
        host = host_from_url(url)
        assert (
            host == "db.weoymywxwmiudephpxet.supabase.co"
        ), f"host not recovered from a DSN with an unescaped @ in the password; got {host!r}"
        assert is_loopback(host) is False

    def test_common_dsn_shapes_resolve_correctly(self) -> None:
        cases = {
            "postgresql://u:p@127.0.0.1:5433/db": "127.0.0.1",
            "postgresql://u:p@localhost:5432/db": "localhost",
            "postgres://u:p@db.example.invalid:5432/db": "db.example.invalid",
            "postgresql://u:p@[::1]:5432/db": "::1",
            "postgresql://u:p@db.example.invalid/db": "db.example.invalid",
        }
        for url, expected in cases.items():
            assert host_from_url(url) == expected, f"{url} -> {host_from_url(url)!r}"

    def test_bracketed_password_and_ipv6_hosts_both_resolve(self) -> None:
        """Brackets in the password and a real IPv6 host both take this path."""
        assert (
            host_from_url("postgresql://user:p[w]o@db.example.invalid:5432/postgres")
            == "db.example.invalid"
        )
        assert host_from_url("postgresql://user:p[w]o@[2001:db8::1]:5432/db") == ("2001:db8::1")

    def test_empty_and_unparseable_inputs_do_not_raise(self) -> None:
        assert host_from_url("") == ""
        assert host_from_url("not-a-url") == ""


class TestFailClosed:
    def test_remote_dsn_with_bracketed_password_is_refused(self) -> None:
        """The whole point: this must raise, not wave the connection through."""
        url = "postgresql://user:p[w]o@db.weoymywxwmiudephpxet.supabase.co:5432/postgres"
        with pytest.raises(RemoteDestructiveRefusedError):
            guard_connection(url)

    def test_refusal_does_not_echo_the_password(self) -> None:
        url = "postgresql://user:p[w]o@db.example.invalid:5432/postgres"
        try:
            guard_connection(url)
        except RemoteDestructiveRefusedError as err:
            message = str(err)
            assert "p[w]o" not in message, "refusal echoed the password"
            assert "db.example.invalid" in message, "refusal should name the host"
        else:  # pragma: no cover - the guard must raise
            pytest.fail("guard_connection did not refuse a remote DSN")

    def test_local_dsn_is_still_allowed(self) -> None:
        guard_connection("postgresql://traderos:traderos@127.0.0.1:5433/traderos_test")
        guard_connection("postgresql://u:p@localhost:5432/db")

    def test_opt_in_still_allows_a_remote_dsn(self, monkeypatch) -> None:
        monkeypatch.setenv("TRADEROS_ALLOW_REMOTE_DESTRUCTIVE", "1")
        guard_connection("postgresql://user:p[w]o@db.example.invalid:5432/postgres")

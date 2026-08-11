"""Circuit-breaker resilience: unit + wiring evidence (AS-7 immune system).

Proves the breaker trips on the real failure path, recovers after the timeout,
and that the broker probe exercises the public broker API (not private fields)
end-to-end through the API surface.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import pytest
import requests
from fastapi.testclient import TestClient

from traderos.infrastructure.database.connection import _connect_postgres
from traderos.infrastructure.resilience import ALL_BREAKERS
from traderos.infrastructure.resilience import PG_CB
from traderos.infrastructure.resilience import VAULT_CB
from traderos.infrastructure.resilience import CircuitBreaker
from traderos.infrastructure.resilience import CircuitBreakerConfig
from traderos.infrastructure.resilience import CircuitHalfOpenError
from traderos.infrastructure.resilience import CircuitOpenError
from traderos.infrastructure.resilience import get_breaker_status
from traderos.infrastructure.resilience import reset_all_breakers
from traderos.infrastructure.resilience import with_circuit_breaker
from traderos.infrastructure.secrets import VaultFetchError
from traderos.infrastructure.secrets import VaultSecretProvider
from traderos.interfaces.api import server

if TYPE_CHECKING:
    from traderos.infrastructure.broker_circuit_breaker import CircuitBreakeredBroker


class TestCircuitBreaker:
    def test_closed_succeeds_and_resets_failures(self) -> None:
        breaker = CircuitBreaker(CircuitBreakerConfig(name="t", failure_threshold=3))
        assert breaker.state == "closed"
        assert breaker.call(lambda: 42) == 42
        assert breaker.failure_count == 0

    def test_half_open_error_carries_breaker_name(self) -> None:
        err = CircuitHalfOpenError("broker")
        assert err.name == "broker"
        assert "HALF-OPEN" in str(err)

    def test_opens_after_threshold(self) -> None:
        breaker = CircuitBreaker(CircuitBreakerConfig(name="t", failure_threshold=3))

        def _explode() -> None:
            raise ValueError("boom")

        for _ in range(2):
            with pytest.raises(ValueError):
                breaker.call(_explode)
        assert breaker.state == "closed"
        with pytest.raises(ValueError):
            breaker.call(_explode)
        assert breaker.state == "open"

    def test_open_fails_fast_without_invoking_fn(self) -> None:
        calls = {"n": 0}
        breaker = CircuitBreaker(CircuitBreakerConfig(name="t", failure_threshold=1))

        def _explode() -> None:
            calls["n"] += 1
            raise ValueError("boom")

        with pytest.raises(ValueError):
            breaker.call(_explode)
        assert breaker.state == "open"
        with pytest.raises(CircuitOpenError):
            breaker.call(_explode)
        assert calls["n"] == 1  # not invoked again while open

    def test_half_open_recovers_after_timeout(self) -> None:
        breaker = CircuitBreaker(
            CircuitBreakerConfig(name="t", failure_threshold=1, recovery_timeout=0.05)
        )

        def _explode() -> None:
            raise ValueError("boom")

        with pytest.raises(ValueError):
            breaker.call(_explode)
        assert breaker.state == "open"
        time.sleep(0.06)
        # half-open trial succeeds twice -> closed
        breaker.call(lambda: 1)
        assert breaker.state == "half-open"
        breaker.call(lambda: 1)
        assert breaker.state == "closed"
        assert breaker.failure_count == 0

    def test_reset_restores_closed(self) -> None:
        breaker = CircuitBreaker(CircuitBreakerConfig(name="t", failure_threshold=1))

        def _explode() -> None:
            raise ValueError("boom")

        with pytest.raises(ValueError):
            breaker.call(_explode)
        assert breaker.state == "open"
        breaker.reset()
        assert breaker.state == "closed"
        assert breaker.call(lambda: 7) == 7

    def test_failure_not_matching_expected_exception_ignored(self) -> None:
        breaker = CircuitBreaker(
            CircuitBreakerConfig(name="t", failure_threshold=2, expected_exception=(OSError,))
        )
        with pytest.raises(ValueError):
            breaker.call(lambda: (_ for _ in ()).throw(ValueError("ignored")))
        assert breaker.failure_count == 0


class TestWithCircuitBreaker:
    def test_wraps_and_succeeds(self) -> None:
        breaker = CircuitBreaker(CircuitBreakerConfig(name="t", failure_threshold=1))

        @with_circuit_breaker(breaker)
        def double(x: int) -> int:
            return x * 2

        assert double(21) == 42

    def test_normal_failure_counts_towards_trip(self) -> None:
        breaker = CircuitBreaker(CircuitBreakerConfig(name="t", failure_threshold=2))

        @with_circuit_breaker(breaker)
        def explode() -> None:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            explode()
        assert breaker.state == "closed"
        with pytest.raises(RuntimeError):
            explode()
        assert breaker.state == "open"
        with pytest.raises(CircuitOpenError):
            explode()

    def test_timeout_raised_and_trips_breaker(self) -> None:
        breaker = CircuitBreaker(CircuitBreakerConfig(name="t", failure_threshold=1))

        @with_circuit_breaker(breaker, timeout=0.05)
        def slow() -> None:
            time.sleep(0.5)

        with pytest.raises(TimeoutError):
            slow()
        assert breaker.state == "open"

    def test_timeout_works_from_non_main_thread(self) -> None:
        """SIGALRM crashes from worker threads; the resolver must be thread-safe."""
        breaker = CircuitBreaker(CircuitBreakerConfig(name="t", failure_threshold=1))
        captured: dict[str, str] = {}

        @with_circuit_breaker(breaker, timeout=0.05)
        def slow() -> None:
            time.sleep(0.5)

        def _run() -> None:
            try:
                slow()
                captured["err"] = "no error"
            except TimeoutError:
                captured["err"] = "timeout"

        t = threading.Thread(target=_run)
        t.start()
        t.join(2.0)
        assert captured.get("err") == "timeout"
        assert breaker.state == "open"


class TestBreakerRegistry:
    def test_breaks_are_preconfigured_for_all_dependencies(self) -> None:
        for name in ("broker", "vault", "postgres"):
            assert name in ALL_BREAKERS
        assert get_breaker_status()["broker"]["state"] == "closed"

    def test_reset_all_restores_closed(self) -> None:
        def _explode() -> None:
            raise RuntimeError("boom")

        for cb in ALL_BREAKERS.values():
            # Trip every preconfigured breaker through its own failure path.
            for _ in range(cb.threshold):
                with pytest.raises(RuntimeError):
                    cb.call(_explode)
            assert cb.state == "open"
        reset_all_breakers()
        for cb in ALL_BREAKERS.values():
            assert cb.state == "closed"
            assert cb.failure_count == 0


class TestBrokerProbe:
    """End-to-end: the probe must pass through the public broker API."""

    @pytest.fixture(autouse=True)
    def _clean(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_PATH", ":memory:")
        monkeypatch.delenv("TRADEROS_API_KEY", raising=False)
        monkeypatch.delenv("TRADEROS_ADMIN_API_KEY", raising=False)
        monkeypatch.delenv("TRADEROS_OPERATOR_API_KEY", raising=False)
        monkeypatch.delenv("TRADEROS_VIEWER_API_KEY", raising=False)
        from traderos.interfaces.api import security

        security.reset_authenticator()
        server._orch_cache.clear()
        reset_all_breakers()
        yield
        server._orch_cache.clear()
        reset_all_breakers()
        security.reset_authenticator()

    @pytest.fixture()
    def client(self) -> TestClient:
        return TestClient(server.build_app())

    def test_broker_probe_happy_path(self, client: TestClient) -> None:
        resp = client.get("/v1/probes/broker")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True, body
        assert body["latency_ms"] >= 0
        assert "place=" in body["detail"]

    def test_probe_uses_public_api_and_leaves_no_orders(self, client: TestClient) -> None:
        client.get("/v1/probes/broker")
        orch = server.create_orchestrator()
        assert orch.broker.get_open_orders() == []

    def test_probes_summary_shape(self, client: TestClient) -> None:
        resp = client.get("/v1/probes")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["broker"]["ok"] is True

    def test_probes_are_auth_aware(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRADEROS_ADMIN_API_KEY", "admin-k" * 8)
        resp = TestClient(server.build_app()).get("/v1/probes/broker")
        assert resp.status_code == 401

    def test_open_circuit_fails_fast_without_touching_broker(self, client: TestClient) -> None:
        """Constitution #4: a refused order must never reach the real broker.

        The live production path (orch.broker) is the wrapped broker; with the
        circuit open the submission must fail fast and leave no orders behind.
        """
        from traderos.infrastructure.resilience import BROKER_CB

        orch = server.create_orchestrator()
        broker = orch.broker
        from traderos.infrastructure.broker_circuit_breaker import CircuitBreakeredBroker

        assert isinstance(broker, CircuitBreakeredBroker)

        # Trip the shared broker circuit on its real failure path.
        def _explode() -> None:
            raise RuntimeError("boom")

        for _ in range(BROKER_CB.threshold):
            with pytest.raises(RuntimeError):
                BROKER_CB.call(_explode)
        assert BROKER_CB.state == "open"

        from traderos.infrastructure.resilience import CircuitOpenError

        with pytest.raises(CircuitOpenError):
            broker.place_limit_order(orch.market_ids[0], "buy", 1.0, 0.01, close_price=None)
        assert broker.get_open_orders() == []


class TestCircuitBreakeredBroker:
    """Every order-modifying method of the boundary wrapper delegates to the
    inner broker through the shared BROKER_CB surface."""

    @pytest.fixture(autouse=True)
    def _reset_breaker(self) -> None:
        reset_all_breakers()
        yield
        reset_all_breakers()

    @pytest.fixture()
    def broker(self) -> CircuitBreakeredBroker:
        from traderos.domain.adapters.broker_adapter import FillResult
        from traderos.infrastructure.broker_circuit_breaker import CircuitBreakeredBroker

        class _Inner:
            def place_market_order(self, *a, **k):
                return FillResult(True, 1.0, 100.0, 0.0, "filled", "m1")

            def place_limit_order(self, *a, **k):
                return FillResult(False, 0.0, 0.0, 1.0, "pending", "")

            def cancel_order(self, order_id):
                return FillResult(True, 0.0, 0.0, 0.0, "cancelled", order_id)

            def place_stop_order(self, *a, **k):
                return FillResult(False, 0.0, 0.0, 1.0, "pending", "")

            def place_trailing_stop_order(self, *a, **k):
                return FillResult(False, 0.0, 0.0, 1.0, "pending", "")

            def modify_order(self, order_id, **k):
                return FillResult(True, 0.0, 0.0, 0.0, "modified", order_id)

            def get_account_balance(self):
                return 10000.0

            def get_positions(self):
                return []

            def get_open_orders(self):
                return []

        return CircuitBreakeredBroker(_Inner())

    def test_stop_order_delegates(self, broker) -> None:
        import uuid

        res = broker.place_stop_order(uuid.uuid4(), "buy", 1.0, 90.0, market_price=100.0)
        assert res.status == "pending"

    def test_trailing_stop_order_delegates(self, broker) -> None:
        import uuid

        res = broker.place_trailing_stop_order(uuid.uuid4(), "buy", 1.0, 0.01, market_price=100.0)
        assert res.status == "pending"

    def test_modify_order_delegates(self, broker) -> None:
        res = broker.modify_order("ord1", qty=2.0, limit_price=101.0)
        assert res.status == "modified"

    def test_reads_pass_through_unwrapped(self, broker) -> None:
        assert broker.get_account_balance() == 10000.0
        assert broker.get_positions() == []
        assert broker.get_open_orders() == []


class TestVaultCircuitWiring:
    """VAULT_CB must trip on the real Vault fetch path and refuse fast.

    A missing/forbidden key (4xx) is NOT an outage — it returns None and never
    counts. Only transport failure / 5xx / corrupt body raises VaultFetchError
    and, once the breaker opens, the protected call refuses with
    CircuitOpenError (fail closed, never a silent demotion).
    """

    @pytest.fixture(autouse=True)
    def _reset_breakers(self) -> None:
        reset_all_breakers()
        yield
        reset_all_breakers()

    def test_network_outage_opens_breaker_and_fails_fast(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = VaultSecretProvider(url="http://127.0.0.1:1", token="t")

        def _refuse(*_args: object, **_kwargs: object) -> None:
            raise requests.ConnectionError("vault refused")

        monkeypatch.setattr(provider._session, "get", _refuse)
        for _ in range(VAULT_CB.threshold):
            with pytest.raises(VaultFetchError):
                provider.get("api/key")
        assert VAULT_CB.state == "open"
        with pytest.raises(CircuitOpenError):
            provider.get("api/key")

    def test_5xx_is_an_outage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = VaultSecretProvider(url="http://127.0.0.1:1", token="t")

        class _Resp503:
            status_code = 503

        monkeypatch.setattr(provider._session, "get", lambda *_a, **_k: _Resp503())
        with pytest.raises(VaultFetchError):
            provider.get("api/key")
        assert VAULT_CB.failure_count == 1

    def test_404_missing_key_is_not_an_outage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = VaultSecretProvider(url="http://127.0.0.1:1", token="t")

        class _Resp404:
            status_code = 404

        monkeypatch.setattr(provider._session, "get", lambda *_a, **_k: _Resp404())
        assert provider.get("no/such/key") is None
        assert provider.get("no/such/key") is None
        assert VAULT_CB.state == "closed"
        assert VAULT_CB.failure_count == 0

    def test_recovers_after_outage_ends(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from traderos.infrastructure import resilience as resmod

        clock = {"now": 1_000_000.0}
        monkeypatch.setattr(resmod.time, "time", lambda: clock["now"])
        provider = VaultSecretProvider(url="http://127.0.0.1:1", token="t")
        mode = {"fail": True}

        def _transport(*_args: object, **_kwargs: object) -> requests.Response:
            if mode["fail"]:
                raise requests.ConnectionError("refused")
            resp = requests.Response()
            resp.status_code = 200
            resp._content = b'{"data":{"data":{"value":"live-key"}}}'
            return resp

        monkeypatch.setattr(provider._session, "get", _transport)
        for _ in range(VAULT_CB.threshold):
            with pytest.raises(VaultFetchError):
                provider.get("api/key")
        assert VAULT_CB.state == "open"

        mode["fail"] = False
        clock["now"] += VAULT_CB.recovery_seconds + 1
        assert provider.get("api/key") == "live-key"  # half-open trial succeeds
        provider.get("api/key")  # second consecutive success
        assert VAULT_CB.state == "closed"
        assert VAULT_CB.failure_count == 0


class TestPostgresCircuitWiring:
    """PG_CB must trip on the real psycopg2.connect path and refuse fast.

    A missing driver (ImportError) is a packaging bug, NOT a database outage,
    and must never trip PG_CB — only genuine connect failures do.
    """

    @pytest.fixture(autouse=True)
    def _reset_breakers(self) -> None:
        reset_all_breakers()
        yield
        reset_all_breakers()

    def test_connect_failures_open_breaker_and_refuse(self, monkeypatch) -> None:
        psycopg2 = pytest.importorskip("psycopg2")

        def _boom(_dsn: str) -> None:
            raise psycopg2.OperationalError("connection refused")

        monkeypatch.setattr(psycopg2, "connect", _boom)
        for _ in range(PG_CB.threshold):
            with pytest.raises(psycopg2.OperationalError):
                _connect_postgres("postgresql://u:p@h:1/db")
        assert PG_CB.state == "open"
        with pytest.raises(CircuitOpenError):
            _connect_postgres("postgresql://u:p@h:1/db")

    def test_import_error_does_not_trip_breaker(self, monkeypatch) -> None:
        import builtins

        real_import = builtins.__import__

        def _guarded(name: str, *args: object, **kwargs: object) -> object:
            if name == "psycopg2":
                raise ImportError("No module named 'psycopg2'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _guarded)
        with pytest.raises(ImportError):
            _connect_postgres("postgresql://u:p@h/db")
        assert PG_CB.state == "closed"
        assert PG_CB.failure_count == 0

    def test_recovers_after_connect_succeeds(self, monkeypatch) -> None:
        from traderos.infrastructure import resilience as resmod

        psycopg2 = pytest.importorskip("psycopg2")
        clock = {"now": 1_000_000.0}
        monkeypatch.setattr(resmod.time, "time", lambda: clock["now"])
        mode = {"fail": True}

        class _FakeConn:
            def __init__(self) -> None:
                self.autocommit = None

        def _transport(_dsn: str) -> _FakeConn:
            if mode["fail"]:
                raise psycopg2.OperationalError("refused")
            return _FakeConn()

        monkeypatch.setattr(psycopg2, "connect", _transport)
        for _ in range(PG_CB.threshold):
            with pytest.raises(psycopg2.OperationalError):
                _connect_postgres("postgresql://u:p@h/db")
        assert PG_CB.state == "open"

        mode["fail"] = False
        clock["now"] += PG_CB.recovery_seconds + 1
        conn = _connect_postgres("postgresql://u:p@h/db")
        assert conn.autocommit is False
        _connect_postgres("postgresql://u:p@h/db")
        assert PG_CB.state == "closed"
        assert PG_CB.failure_count == 0

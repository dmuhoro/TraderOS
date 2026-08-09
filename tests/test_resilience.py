"""Circuit-breaker resilience: unit + wiring evidence (AS-7 immune system).

Proves the breaker trips on the real failure path, recovers after the timeout,
and that the broker probe exercises the public broker API (not private fields)
end-to-end through the API surface.
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from traderos.infrastructure.resilience import ALL_BREAKERS
from traderos.infrastructure.resilience import CircuitBreaker
from traderos.infrastructure.resilience import CircuitBreakerConfig
from traderos.infrastructure.resilience import CircuitOpenError
from traderos.infrastructure.resilience import get_breaker_status
from traderos.infrastructure.resilience import reset_all_breakers
from traderos.infrastructure.resilience import with_circuit_breaker
from traderos.interfaces.api import server


class TestCircuitBreaker:
    def test_closed_succeeds_and_resets_failures(self) -> None:
        breaker = CircuitBreaker(CircuitBreakerConfig(name="t", failure_threshold=3))
        assert breaker.state == "closed"
        assert breaker.call(lambda: 42) == 42
        assert breaker.failure_count == 0

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

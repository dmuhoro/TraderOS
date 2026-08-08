from __future__ import annotations

import http.client
import threading
import time

import pytest
from fastapi.testclient import TestClient

from traderos.infrastructure.auth import APIKeyAuthenticator
from traderos.interfaces.api import security
from traderos.interfaces.api import server
from traderos.interfaces.api import sse_tokens


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PATH", ":memory:")
    monkeypatch.delenv("SSE_TOKEN_SECRET", raising=False)
    server._orch_cache.clear()
    server._api_key = None
    security.reset_authenticator()
    sse_tokens.reset()
    yield
    server._orch_cache.clear()
    security.reset_authenticator()
    sse_tokens.reset()


@pytest.fixture()
def auth_client() -> TestClient:
    security.set_authenticator(
        APIKeyAuthenticator(admin_keys=("secret-key-123456",), operator_keys=(), viewer_keys=())
    )
    return TestClient(server.build_app())


class TestEventTokenObject:
    def test_fresh_token_validates_once(self) -> None:
        token, _ = sse_tokens.mint()
        assert sse_tokens.validate(token) is True
        assert sse_tokens.validate(token) is False  # single-use

    def test_malformed_and_bogus_tokens_rejected(self) -> None:
        assert sse_tokens.validate(None) is False
        assert sse_tokens.validate("") is False
        assert sse_tokens.validate("garbage") is False
        assert sse_tokens.validate("a:b:c:d:e") is False
        assert sse_tokens.validate("other:n:999:deadbeef") is False

    def test_tampered_signature_rejected(self) -> None:
        token, _ = sse_tokens.mint()
        good_signature = token.rsplit(":", 1)[1]
        bad = token.replace(good_signature, "f" * len(good_signature))
        assert sse_tokens.validate(bad) is False

    def test_expired_token_rejected(self) -> None:
        token, expires_at = sse_tokens.mint(ttl_seconds=60)
        assert sse_tokens.validate(token, now=expires_at + 1) is False

    def test_peek_does_not_consume(self) -> None:
        token, _ = sse_tokens.mint()
        assert sse_tokens.peek(token) is True
        assert sse_tokens.validate(token) is True  # still fresh (peek is non-mutating)

    def test_peek_rejects_garbage_and_expired(self) -> None:
        token, expires_at = sse_tokens.mint(ttl_seconds=60)
        assert sse_tokens.peek("bogus") is False
        assert sse_tokens.peek(token, now=expires_at + 1) is False


class TestEventTokenEndpoint:
    def test_mint_requires_api_key(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/v1/events/token")
        assert resp.status_code in (401, 403)
        body = resp.json()
        assert "error" in body

    def test_mint_returns_typed_token(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/v1/events/token", headers={"X-API-Key": "secret-key-123456"})
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"token", "expires_at"}
        assert body["token"]
        assert body["expires_at"] > int(time.time())

    def test_events_sse_rejects_reused_and_absent_and_bogus_token(
        self, auth_client: TestClient
    ) -> None:
        # TestClient buffers the SSE body under BaseHTTPMiddleware, so the
        # consuming route guard is proven here on top of the real checkout.
        token = auth_client.get(
            "/v1/events/token", headers={"X-API-Key": "secret-key-123456"}
        ).json()["token"]
        # a replayed token is rejected by the route dependency (single-use)
        assert sse_tokens.validate(token) is True  # first (mock) use consumed
        assert auth_client.get(f"/v1/events?token={token}").status_code == 401
        assert auth_client.get("/v1/events").status_code == 401
        assert auth_client.get("/v1/events?token=bogus").status_code == 401

    def test_sse_token_does_not_open_other_endpoints(self, auth_client: TestClient) -> None:
        token = auth_client.get(
            "/v1/events/token", headers={"X-API-Key": "secret-key-123456"}
        ).json()["token"]
        for path in ("/v1/positions", "/v1/orders", "/v1/portfolio"):
            resp = auth_client.get(f"{path}?token={token}")
            assert resp.status_code == 401, path

    def test_real_uvicorn_serves_sse_with_valid_token(self) -> None:
        """The minted token opens the real SSE stream over uvicorn (production
        path): header auth to mint, query-param token to subscribe, then the
        consumed token is rejected on replay."""
        security.set_authenticator(
            APIKeyAuthenticator(admin_keys=("secret-key-123456",), operator_keys=(), viewer_keys=())
        )
        server._orch_cache.clear()
        app = server.build_app()
        import uvicorn

        config = uvicorn.Config(app, host="127.0.0.1", port=8123, log_level="warning")
        srv = uvicorn.Server(config)
        thread = threading.Thread(target=srv.run, daemon=True)
        thread.start()
        try:
            timeout_at = time.time() + 10
            while not getattr(srv, "started", False):
                if time.time() > timeout_at:
                    pytest.fail("uvicorn did not start")
                time.sleep(0.1)
            # mint over the real HTTP seam
            conn = http.client.HTTPConnection("127.0.0.1", 8123, timeout=5)
            conn.request("GET", "/v1/events/token", headers={"X-API-Key": "secret-key-123456"})
            resp = conn.getresponse()
            payload = resp.read()
            assert resp.status == 200, payload
            conn.close()
            token = __import__("json").loads(payload)["token"]

            # EventSource-style subscription via query param
            conn = http.client.HTTPConnection("127.0.0.1", 8123, timeout=5)
            conn.request("GET", f"/v1/events?token={token}")
            resp = conn.getresponse()
            assert resp.status == 200
            assert resp.getheader("content-type").startswith("text/event-stream")
            assert resp.readline().decode().startswith("event: snapshot")
            conn.close()

            # consumed token cannot be replayed
            conn = http.client.HTTPConnection("127.0.0.1", 8123, timeout=5)
            conn.request("GET", f"/v1/events?token={token}")
            resp = conn.getresponse()
            assert resp.status == 401
            resp.read()
            conn.close()
        finally:
            srv.should_exit = True
            thread.join(timeout=5)

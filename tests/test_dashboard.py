from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from traderos.infrastructure.auth import APIKeyAuthenticator
from traderos.interfaces.api import security
from traderos.interfaces.api import server

DASHBOARD_DIR = Path(server.__file__).parent / "dashboard"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PATH", ":memory:")
    server._orch_cache.clear()
    security.reset_authenticator()
    yield
    server._orch_cache.clear()
    security.reset_authenticator()


class TestDashboardServed:
    def test_root_redirects_to_dashboard(self) -> None:
        client = TestClient(server.build_app())
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/dashboard/"

    def test_dashboard_index_served(self) -> None:
        client = TestClient(server.build_app())
        resp = client.get("/dashboard/")
        assert resp.status_code == 200
        assert "Finish Line Dashboard" in resp.text
        assert "app.js" in resp.text

    def test_dashboard_assets_served(self) -> None:
        client = TestClient(server.build_app())
        app_js = client.get("/dashboard/app.js")
        css = client.get("/dashboard/style.css")
        content_type = app_js.headers["content-type"]
        assert app_js.status_code == 200
        assert "javascript" in content_type
        assert css.status_code == 200
        assert "text/css" in css.headers["content-type"]

    def test_dashboard_is_open_when_auth_configured(self) -> None:
        security.set_authenticator(
            APIKeyAuthenticator(admin_keys=("secret-key-123456",), operator_keys=(), viewer_keys=())
        )
        client = TestClient(server.build_app())
        assert client.get("/dashboard/").status_code == 200
        assert client.get("/dashboard/app.js").status_code == 200


class TestDashboardSurface:
    def test_assets_reference_expected_api_endpoints(self) -> None:
        app_js = (DASHBOARD_DIR / "app.js").read_text()
        assert "/v1/auth/me" in app_js
        assert "/v1/workflow/advance" in app_js
        assert "/v1/kill-switch/engage" in app_js
        assert "/v1/reports/session" in app_js
        assert "EventSource" in app_js

    def test_index_lists_core_panels(self) -> None:
        html = (DASHBOARD_DIR / "index.html").read_text()
        for panel in (
            "Operator workflow",
            "Portfolio",
            "Positions",
            "Strategy catalog",
            "Session report",
        ):
            assert panel in html

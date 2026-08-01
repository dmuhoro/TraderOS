from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock
from unittest.mock import patch


class TestApiMain:
    def test_build_app_returns_fastapi_app(self) -> None:
        from traderos.interfaces.api.server import build_app

        app = build_app()
        assert app.title == "TraderOS API"
        assert len(app.routes) > 0

    def test_main_entry_point(self) -> None:
        from traderos.interfaces.api.main import main

        assert callable(main)


class TestApiMainRunner:
    def _run_main(self, monkeypatch, port: str = "8000", ssl_key: str | None = None) -> dict:
        from traderos.interfaces.api.main import main

        uvicorn = ModuleType("uvicorn")
        uvicorn.run = MagicMock()
        monkeypatch.setitem(sys.modules, "uvicorn", uvicorn)

        monkeypatch.setenv("PORT", port)
        if ssl_key:
            monkeypatch.setenv("SSL_KEYFILE", "/certs/server.key")
            monkeypatch.setenv("SSL_CERTFILE", "/certs/server.crt")
        else:
            monkeypatch.delenv("SSL_KEYFILE", raising=False)
            monkeypatch.delenv("SSL_CERTFILE", raising=False)

        with patch("traderos.interfaces.api.main.build_app") as build:
            app = MagicMock()
            build.return_value = app
            main()
        return uvicorn.run.call_args

    def test_runs_uvicorn_on_all_interfaces_default_port(self, monkeypatch) -> None:
        _call_args, call_kwargs = self._run_main(monkeypatch)
        assert call_kwargs["host"] == "0.0.0.0"
        assert call_kwargs["port"] == 8000
        assert "ssl_keyfile" not in call_kwargs

    def test_runs_uvicorn_on_port_from_env(self, monkeypatch) -> None:
        _call_args, call_kwargs = self._run_main(monkeypatch, port="9000")
        assert call_kwargs["port"] == 9000

    def test_runs_uvicorn_with_ssl_when_certs_configured(self, monkeypatch) -> None:
        _call_args, call_kwargs = self._run_main(monkeypatch, ssl_key="yes")
        assert call_kwargs["ssl_keyfile"] == "/certs/server.key"
        assert call_kwargs["ssl_certfile"] == "/certs/server.crt"

    def test_uvicorn_run_receives_built_app(self, monkeypatch) -> None:
        call_args, _call_kwargs = self._run_main(monkeypatch)
        app_arg = call_args[0]
        assert app_arg is not None

from __future__ import annotations


class TestApiMain:
    def test_build_app_returns_fastapi_app(self) -> None:
        from traderos.interfaces.api.server import build_app

        app = build_app()
        assert app.title == "TraderOS API"
        assert len(app.routes) > 0

    def test_main_entry_point(self) -> None:
        from traderos.interfaces.api.main import main

        assert callable(main)

from __future__ import annotations

from traderos.infrastructure.database.connection import resolve_backend


class TestResolveBackend:
    def test_sqlite_default(self):
        assert resolve_backend("") == "sqlite"
        assert resolve_backend("sqlite:///foo.db") == "sqlite"

    def test_postgres_urls(self):
        assert resolve_backend("postgresql://user:pass@host/db") == "postgres"
        assert resolve_backend("postgres://user:pass@host/db") == "postgres"

    def test_postgres_raises_without_psycopg2(self):
        import sys

        if "psycopg2" in sys.modules:
            pass
        else:
            from traderos.infrastructure.database.connection import _connect_postgres

            try:
                _connect_postgres("postgresql://localhost/test")
                raise AssertionError("should have raised ImportError")
            except ImportError:
                pass

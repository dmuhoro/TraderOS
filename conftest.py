import os
from collections.abc import Iterator

import pytest


def pytest_sessionstart(session) -> None:
    os.environ.setdefault("DB_PATH", "test_trader.db")


@pytest.fixture(autouse=True)  # pyright: ignore[reportUntypedFunctionDecorator]
def lean_breakers() -> Iterator[None]:
    """Scope breaker state to one test.

    The breakers (BROKER_CB/VAULT_CB/PG_CB) are process-global singletons that
    trip tests intentionally open. Without a reset at every test boundary, an
    earlier test's thrown failure leaks into a later test that assumes a clean
    slate — an order-dependent flake (WP4). Resetting before AND after each
    test makes breaker state strictly per-test; tests that assert intra-test
    transitions (closed -> open -> recovery) still work because resets only
    happen at boundaries.
    """
    from traderos.infrastructure.resilience import reset_all_breakers

    reset_all_breakers()
    yield
    reset_all_breakers()


def pytest_sessionfinish(session, exitstatus):
    test_dbs = [
        "test_trader.db",
        "test_sprint1.db",
    ]
    for db in test_dbs:
        if os.path.exists(db):
            os.remove(db)

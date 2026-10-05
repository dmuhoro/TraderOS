import os
from collections.abc import Iterator

import pytest

# The throwaway database the suite is allowed to use. Local by construction:
# the tests DROP and TRUNCATE constantly, so this must never be a shared or
# remote instance.
LOCAL_TEST_DATABASE_URL = os.getenv(
    "TRADEROS_TEST_DATABASE_URL",
    "postgresql://traderos:traderos@127.0.0.1:5433/traderos_test",
)


def _retarget_remote_database_url() -> str | None:
    """Repoint an inherited remote DATABASE_URL at the local throwaway.

    ``config_loader`` calls ``load_dotenv()`` at import time, so every test
    process inherits the developer's real DATABASE_URL from ``.env`` -- invisible
    at the call site and impossible to spot by reading the test. Any test that
    reaches the database without passing an explicit config then dials
    production. This is not hypothetical: ``test_factory.py`` builds a real
    orchestrator and reached the production host on every run.

    Only a REMOTE URL is replaced. A sqlite path, an unset URL, or a URL already
    pointing at loopback is left exactly as the test author intended, so this
    never silently changes which backend a test exercises -- it only stops a
    test from reaching something that is not ours to destroy.

    ``safety_guard.guard_connection`` independently refuses remote connections
    under pytest. That interlock is the backstop; this is the fix that lets the
    suite actually run.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgres://")):
        return None
    from traderos.infrastructure.database.safety_guard import host_from_url
    from traderos.infrastructure.database.safety_guard import is_loopback

    host = host_from_url(url)
    if is_loopback(host):
        return None
    os.environ["DATABASE_URL"] = LOCAL_TEST_DATABASE_URL
    return host


def pytest_sessionstart(session) -> None:
    os.environ.setdefault("DB_PATH", "test_trader.db")


def pytest_configure(config) -> None:
    """Retarget an inherited remote DATABASE_URL before any test module imports.

    ``config_loader.load()`` calls ``load_dotenv()``, which populates
    ``os.environ["DATABASE_URL"]`` from ``.env``. That only happens when a
    ``Config`` is actually loaded, which is far too late: by then the value is
    already in play. Load the dotenv explicitly here so the URL can be inspected
    and replaced before any test module is imported.

    ``load_dotenv`` does not override an already-set variable, so this call
    cannot clobber a URL the operator exported deliberately, and the value we
    substitute afterwards survives every later ``Config.load()``.
    """
    os.environ.setdefault("DB_PATH", "test_trader.db")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:  # noqa: BLE001 - never block collection on this
        return
    host = _retarget_remote_database_url()
    if host is not None:
        print(
            f"\n[conftest] DATABASE_URL pointed at remote host {host!r}; "
            f"retargeted to the local throwaway {LOCAL_TEST_DATABASE_URL!r}.\n"
        )


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

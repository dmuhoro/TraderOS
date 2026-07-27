import subprocess
import sys


def _imports_module(module_path: str) -> set[str]:
    subprocess.run(
        [sys.executable, "-c", f"import {module_path}; print('OK')"],
        capture_output=True,
        text=True,
        cwd="src",
        check=False,
    )
    return set()


DOMAIN_IMPORT = "import traderos.domain; print(traderos.domain.__file__)"
DOMAIN_REPOS_IMPORT = "import traderos.domain.repositories; print('OK')"
INFR_CORE = (
    "from traderos.infrastructure.repositories.in_memory "
    "import InMemoryMarketRepository; print('OK')"
)
ENTITY_IMPORT = (
    "from traderos.domain import Market, Candle, Signal, " "Strategy, Trade; print('OK')"
)
REPO_IMPORT = (
    "from traderos.domain.repositories import " "MarketRepository, CandleRepository; print('OK')"
)
EVENT_IMPORT = (
    "from traderos.infrastructure.events import " "EventBus, InMemoryEventBus; print('OK')"
)
LOGGING_IMPORT = "from traderos.infrastructure.logging " "import StructuredLogger; print('OK')"
EXC_IMPORT = "from traderos.domain.exceptions import " "TraderOSError, DomainError; print('OK')"


class TestArchitectureLayers:
    def test_domain_does_not_import_infrastructure(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", DOMAIN_IMPORT],
            capture_output=True,
            text=True,
            cwd="src",
            check=False,
        )
        assert result.returncode == 0, f"Domain import failed: {result.stderr}"

    def test_domain_does_not_import_infrastructure_directly(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", DOMAIN_REPOS_IMPORT],
            capture_output=True,
            text=True,
            cwd="src",
            check=False,
        )
        assert result.returncode == 0, f"Domain repos import failed: {result.stderr}"

    def test_infrastructure_can_import_domain(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", INFR_CORE],
            capture_output=True,
            text=True,
            cwd="src",
            check=False,
        )
        assert result.returncode == 0, f"Infra->domain import failed: {result.stderr}"

    def test_all_entity_imports_work(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", ENTITY_IMPORT],
            capture_output=True,
            text=True,
            cwd="src",
            check=False,
        )
        assert result.returncode == 0, f"Entity import failed: {result.stderr}"

    def test_all_repository_imports_work(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", REPO_IMPORT],
            capture_output=True,
            text=True,
            cwd="src",
            check=False,
        )
        assert result.returncode == 0, f"Repo import failed: {result.stderr}"

    def test_all_inmemory_repo_imports_work(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", INFR_CORE],
            capture_output=True,
            text=True,
            cwd="src",
            check=False,
        )
        assert result.returncode == 0, f"InMemory import failed: {result.stderr}"

    def test_event_bus_imports_work(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", EVENT_IMPORT],
            capture_output=True,
            text=True,
            cwd="src",
            check=False,
        )
        assert result.returncode == 0, f"Event bus import failed: {result.stderr}"

    def test_logging_imports_work(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", LOGGING_IMPORT],
            capture_output=True,
            text=True,
            cwd="src",
            check=False,
        )
        assert result.returncode == 0, f"Logging import failed: {result.stderr}"

    def test_exceptions_import_works(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", EXC_IMPORT],
            capture_output=True,
            text=True,
            cwd="src",
            check=False,
        )
        assert result.returncode == 0, f"Exceptions import failed: {result.stderr}"

from __future__ import annotations

import uuid

import pytest

from traderos.application.factory import build_orchestrator
from traderos.application.orchestrator import TradingMode
from traderos.application.orchestrator import TradingOrchestrator
from traderos.domain.services.paper_trading_service import PaperTradingService
from traderos.domain.services.portfolio_service import PortfolioService
from traderos.domain.services.signal_service import SignalService
from traderos.infrastructure.config.config_loader import Config
from traderos.infrastructure.secrets import SecretRotator


class TestServiceFactory:
    def test_build_paper_orchestrator(self) -> None:
        orch = build_orchestrator(mode="paper")
        assert isinstance(orch, TradingOrchestrator)
        assert orch.mode == TradingMode.PAPER
        assert isinstance(orch.signal_service, SignalService)
        assert isinstance(orch.portfolio_service, PortfolioService)
        assert orch.paper is not None
        assert isinstance(orch.paper, PaperTradingService)

    def test_build_paper_orchestrator_has_real_deps(self) -> None:
        orch = build_orchestrator(mode="paper")
        assert orch.signal_service.repo is not None
        assert orch.portfolio_service.trade_repo is not None
        assert orch.portfolio_service.position_repo is not None

    def test_build_with_config(self) -> None:
        cfg = Config(db_path=":memory:", log_level="DEBUG")
        orch = build_orchestrator(mode="paper", config=cfg)
        assert isinstance(orch, TradingOrchestrator)

    def test_build_with_market_ids(self) -> None:
        mid = uuid.uuid4()
        orch = build_orchestrator(
            mode="paper",
            market_ids=[mid],
        )
        assert mid in orch.market_ids

    def test_orchestrator_round_trip(self) -> None:
        orch = build_orchestrator(mode="paper")
        orch.start()
        assert orch.running
        assert orch.health.get_status("orchestrator") is True
        result = orch.run_cycle(uuid.uuid4(), 100.0)
        assert result.signals >= 0
        assert result.trades >= 0
        orch.stop()
        assert not orch.running

    def test_orchestrator_get_status(self) -> None:
        orch = build_orchestrator(mode="paper")
        status = orch.get_status()
        assert status["mode"] == "paper"
        assert status["running"] is False

    def test_secret_rotator_wired(self) -> None:
        orch = build_orchestrator(mode="paper")
        assert isinstance(orch.secret_rotator, SecretRotator)
        status = orch.get_status()
        assert "secret_rotation" in status
        assert status["secret_rotation"]["total_secrets"] >= 0

    def test_secret_rotator_lifecycle(self) -> None:
        orch = build_orchestrator(mode="paper")
        assert orch.secret_rotator is not None
        orch.start()
        assert orch.secret_rotator._bg_thread is not None
        assert orch.secret_rotator._bg_thread.is_alive()
        orch.stop()
        assert not orch.secret_rotator._bg_thread.is_alive()

    def test_all_services_wired(self) -> None:
        orch = build_orchestrator(mode="paper")
        assert orch.analysis is not None
        assert orch.broker is not None
        assert orch.backtest is not None
        assert orch.event_bus is not None
        assert orch.health is not None
        assert orch.audit is not None
        assert orch.metrics is not None
        assert orch.notifications is not None
        assert orch.run_manifest is not None


def _pg_reachable(timeout: int = 3) -> bool:
    try:
        import psycopg2

        conn = psycopg2.connect(
            "host=localhost port=5433 dbname=traderos_test user=traderos password=traderos",
            connect_timeout=timeout,
        )
        conn.close()
        return True
    except Exception:  # noqa: BLE001 — environment probe, never fatal
        return False


_PG_SKIP = pytest.mark.skipif(
    not _pg_reachable(),
    reason="Postgres not reachable — PG-backed factory parity skipped, not passed",
)


@_PG_SKIP
class TestPostgresBackedFactory:
    """A5 parity gate: when DATABASE_URL points at Postgres, the factory must
    build the strategy/workflow/backtest repos on Postgres (not degrade to
    in-memory), so a deployed store is never silently demoted."""

    def test_factory_uses_postgres_strategy_repo(self, monkeypatch) -> None:
        from traderos.infrastructure.repositories.postgres.strategies import (
            PostgresStrategyRepository,
        )

        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql://traderos:traderos@localhost:5433/traderos_test",
        )
        orch = build_orchestrator(mode="paper")
        assert isinstance(orch.strategy_repository, PostgresStrategyRepository)

    def test_factory_uses_postgres_workflow_repo(self, monkeypatch) -> None:
        from traderos.infrastructure.repositories.postgres.workflows import (
            PostgresOperatorWorkflowRepository,
        )

        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql://traderos:traderos@localhost:5433/traderos_test",
        )
        orch = build_orchestrator(mode="paper")
        assert isinstance(orch.workflow_repository, PostgresOperatorWorkflowRepository)

    def test_factory_has_no_in_memory_backtest_results_on_pg(self, monkeypatch) -> None:
        from traderos.infrastructure.repositories.postgres.strategies import (
            PostgresBacktestResultRepository,
        )

        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql://traderos:traderos@localhost:5433/traderos_test",
        )
        orch = build_orchestrator(mode="paper")
        assert isinstance(orch.strategy_catalog.backtest_results, PostgresBacktestResultRepository)


@pytest.mark.skipif(
    not _pg_reachable(),
    reason="Postgres not reachable — A5 parity drill skipped, not passed",
)
class TestPostgresParityDrill:
    def test_postgres_parity_drill_passes(self) -> None:
        import subprocess
        import sys
        from pathlib import Path

        script = (
            Path(__file__).resolve().parents[2]
            / "scripts"
            / "evidence"
            / "run_postgres_parity_drill.py"
        )
        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[2],
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "VERDICT: PASS" in proc.stdout
        assert "in_memory_fallback=No" in proc.stdout

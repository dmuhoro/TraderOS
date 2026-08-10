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


def _live_rails_config() -> Config:
    """A LIVE-mode config that clears the WP11 production-risk-rail gate so a
    boot reaches the A6 credential check these tests prove."""
    return Config(
        db_path=":memory:",
        _raw_settings={
            "risk": {
                "daily_loss_pct": 0.02,
                "max_gross_exposure": 1.0,
                "max_position_size": 0.25,
                "max_positions_total": 10,
                "max_data_staleness_seconds": 300.0,
                "allowed_markets": ["SPY"],
                "require_allowlist": True,
            }
        },
    )


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

    def test_secret_rotator_brings_real_audit_and_metrics(self, monkeypatch) -> None:
        """A6: the rotator must be wired to the orchestrator's real audit/metrics
        so secret access/rotation is genuinely persisted on the production path
        (not an isolated unit-test-only behaviour)."""
        orch = build_orchestrator(mode="paper")
        assert orch.secret_rotator is not None
        assert orch.secret_rotator._audit is orch.audit
        assert orch.secret_rotator._metrics is orch.metrics
        monkeypatch.setenv("A6_DRILL_KEY", "some-secret-value")
        orch.secret_rotator.get("A6_DRILL_KEY")
        monkeypatch.setenv("A6_DRILL_KEY", "rotated-value")
        orch.secret_rotator.rotate("A6_DRILL_KEY")
        actions = [e.action for e in orch.audit.get_entries(limit=100)]
        assert "secret.accessed" in actions
        assert "secret.rotated" in actions
        assert orch.metrics.get_counter("secret.accessed.read.provider") > 0
        assert orch.metrics.get_counter("secret.rotated") > 0

    def test_live_mode_fails_closed_without_broker_credentials(self, monkeypatch) -> None:
        """A6 fail-closed gate: LIVE must never silently degrade to paper when
        broker credentials are absent via the secret manager/env. WP11 rails are
        supplied so this test exercises the credential gate specifically."""
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="ALPACA_API_KEY and ALPACA_SECRET_KEY"):
            build_orchestrator(mode="live", config=_live_rails_config())

    def test_live_mode_papers_never_a_fallback(self, monkeypatch) -> None:
        """A6: with credentials present but an unusable adapter, LIVE raises
        rather than surfacing PaperBrokerAdapter (fail-closed, no silent demotion)."""
        monkeypatch.setenv("ALPACA_API_KEY", "AK")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "SK")
        monkeypatch.setenv("ALPACA_PAPER", "false")
        import traderos.infrastructure.alpaca_broker as _ab

        monkeypatch.setattr(
            _ab,
            "_TradingClient",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("cannot reach broker")),
        )
        with pytest.raises(RuntimeError, match="LIVE broker init failed"):
            build_orchestrator(mode="live", config=_live_rails_config())

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


class TestSecretLifecycleDrill:
    """A6 suite lock: the secret-lifecycle + fail-closed live drill must pass.
    In-memory only (no network), so it runs unconditionally in CI."""

    def test_secret_lifecycle_drill_passes(self) -> None:
        import subprocess
        import sys
        from pathlib import Path

        script = (
            Path(__file__).resolve().parents[2]
            / "scripts"
            / "evidence"
            / "run_secret_lifecycle_drill.py"
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
        for name in ("access_audited", "rotation_audited_versioned", "live_requires_credentials"):
            assert f"[PASS] {name}" in proc.stdout


class TestOnCallTransportDrill:
    """A7 suite lock: the severity-routed on-call transport drill must pass.
    Uses a real loopback HTTP server (no external network), so it runs in CI."""

    def test_oncall_transport_drill_passes(self) -> None:
        import subprocess
        import sys
        from pathlib import Path

        script = (
            Path(__file__).resolve().parents[2] / "scripts" / "evidence" / "run_oncall_drill.py"
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
        for name in (
            "severity_routing",
            "delivered_on_2xx",
            "fail_closed_critical",
        ):
            assert f"[PASS] {name}" in proc.stdout


class TestUserAccountDrill:
    """B1 suite lock: the fail-closed user/account drill must pass.
    In-memory only (no network), so it runs unconditionally in CI."""

    def test_user_account_drill_passes(self) -> None:
        import subprocess
        import sys
        from pathlib import Path

        script = (
            Path(__file__).resolve().parents[2] / "scripts" / "evidence" / "run_account_drill.py"
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
        for name in ("password_hashed", "fail_closed_credentials", "per_user_api_key"):
            assert f"[PASS] {name}" in proc.stdout

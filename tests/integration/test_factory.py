from __future__ import annotations

import uuid

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

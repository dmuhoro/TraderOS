from __future__ import annotations

import os
import uuid
from datetime import UTC
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from traderos.application.cycle_executor import CycleExecutor
from traderos.application.models import TradingMode
from traderos.domain.exceptions import ConfigError
from traderos.domain.services.backtesting_service import BacktestingService
from traderos.domain.services.data_ingestion_service import DataIngestionService
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.risk_service import RiskService
from traderos.domain.services.signal_service import SignalService
from traderos.infrastructure.config.config_loader import Config


class TestCycleExecutorEdgeCases:
    def test_run_without_data_ingestion(self):
        executor = CycleExecutor(
            mode=TradingMode.PAPER,
            signal_service=MagicMock(spec=SignalService),
            risk_service=RiskService(),
            portfolio_service=MagicMock(),
            execution=ExecutionService(),
            analysis=MagicMock(),
            broker=MagicMock(),
            event_bus=MagicMock(),
            health=MagicMock(),
            audit=MagicMock(),
            metrics=MagicMock(),
            notifications=MagicMock(),
            run_manifest=MagicMock(),
            data_ingestion=None,
        )
        mid = uuid.uuid4()
        result = executor.run(mid, 100.0)
        assert result.market_id == mid
        assert result.signals >= 0


class TestBacktestingServiceEdgeCases:
    def test_no_signals_generated(self):
        svc = BacktestingService(execution=ExecutionService())
        strategy = MagicMock()
        strategy.evaluate.return_value = None
        from datetime import UTC
        from datetime import datetime
        from decimal import Decimal

        from traderos.domain.entities import OHLCV
        from traderos.domain.entities import Candle
        from traderos.domain.entities import Timeframe

        mid = uuid.uuid4()
        candles = [
            Candle(
                market_id=mid,
                ohlcv=OHLCV(
                    open=Decimal(100),
                    high=Decimal(101),
                    low=Decimal(99),
                    close=Decimal(100),
                    volume=Decimal(1000),
                ),
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
                timeframe=Timeframe.DAY_1,
            )
        ]
        result, _ = svc.run(strategy, candles, mid)
        assert result.metrics.total_return == 0.0
        assert result.metrics.win_rate == 0.0


class TestConfigEdgeCases:
    def test_config_with_secret_fields_in_yaml(self):
        with (
            patch("traderos.infrastructure.config.config_loader.Path.exists", return_value=True),
            patch("traderos.infrastructure.config.config_loader.Path.read_text") as mock_read,
        ):
            mock_read.return_value = "alpaca_api_key: fake_key\n"
            cfg = Config.load()
            assert cfg.alpaca_api_key == ""

    def test_config_validate_live_mode_missing_keys(self):
        with patch.dict(os.environ, {"TRADING_MODE": "live"}, clear=True):
            cfg = Config(alpaca_api_key="", alpaca_secret_key="")
            with pytest.raises(ConfigError, match="LIVE mode"):
                cfg.validate()

    def test_config_validate_drawdown_too_high(self):
        with patch.dict(os.environ, {"MAX_DRAWDOWN": "200"}, clear=True):
            cfg = Config()
            with pytest.raises(ConfigError, match="MAX_DRAWDOWN"):
                cfg.validate()

    def test_config_secret_in_yaml_ignored(self, tmp_path, monkeypatch):
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text("alpaca_api_key: fake_key\n")
        for var in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "DB_PATH", "DEFAULT_CASH"):
            monkeypatch.delenv(var, raising=False)
        cfg = Config.load(str(yaml_file))
        assert cfg.alpaca_api_key == ""

    def test_config_string_bools_from_yaml(self, tmp_path):
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text("paper_trading: 'true'\nalpaca_paper: '1'\n")
        cfg = Config.load(str(yaml_file))
        assert cfg.paper_trading is True
        assert cfg.alpaca_paper is True

    def test_config_default_cash_cast_from_yaml(self, tmp_path):
        yaml_file = tmp_path / "settings.yaml"
        yaml_file.write_text("default_cash: 5000\n")
        cfg = Config.load(str(yaml_file))
        assert cfg.default_cash == 5000.0

    def test_config_validate_skips_when_database_url_set(self):
        cfg = Config(database_url="postgresql://host/db")
        cfg.validate()  # must not raise despite empty db_path
        assert cfg.db_path == "data/trader.db"

    def test_config_validate_db_dir_missing(self, tmp_path):
        cfg = Config(db_path=str(tmp_path / "missing_dir" / "trader.db"))
        with pytest.raises(ConfigError, match="db_path directory does not exist"):
            cfg.validate()

    def test_config_validate_forex_symbols_not_list(self):
        cfg = Config(
            db_path=":memory:",
            _raw_settings={"data_collection": {"forex_symbols": "EURUSD"}},
        )
        with pytest.raises(ConfigError, match="forex_symbols must be a list"):
            cfg.validate()

    def test_config_validate_empty_db_path(self):
        cfg = Config(db_path="")
        with pytest.raises(ConfigError, match="db_path must not be empty"):
            cfg.validate()

    def test_config_validate_invalid_log_level(self):
        cfg = Config(db_path=":memory:", log_level="VERBOSE")
        with pytest.raises(ConfigError, match="Invalid log_level"):
            cfg.validate()


class TestDataIngestionServiceEdgeCases:
    def test_fetch_no_collector(self):
        registry = MagicMock()
        registry.get.return_value = None
        svc = DataIngestionService(registry=registry)
        result = svc.fetch_candles(uuid.uuid4(), limit=10)
        assert result == []

    def test_add_multiple_sources(self):
        registry = MagicMock()
        svc = DataIngestionService(registry=registry)
        svc.add_source(uuid.uuid4(), "BTCUSD", "mock")
        svc.add_source(uuid.uuid4(), "ETHUSD", "mock")
        assert len(svc.sources) == 2

    def test_get_latest_close_no_source(self):
        registry = MagicMock()
        svc = DataIngestionService(registry=registry)
        assert svc.get_latest_close(uuid.uuid4()) is None

    def test_get_latest_close_with_data(self):
        registry = MagicMock()
        mock_collector = MagicMock()
        from datetime import datetime

        mock_candle = MagicMock()
        mock_candle.timestamp = datetime(2024, 1, 1, tzinfo=UTC)
        mock_candle.open = 100.0
        mock_candle.high = 101.0
        mock_candle.low = 99.0
        mock_candle.close = 100.5
        mock_candle.volume = 1000.0
        mock_collector.fetch_historical.return_value = [mock_candle]
        registry.get.return_value = mock_collector
        svc = DataIngestionService(registry=registry)
        mid = uuid.uuid4()
        svc.add_source(mid, "BTCUSD", "mock")
        assert svc.get_latest_close(mid) == 100.5

    def test_get_latest_close_empty_feed(self):
        registry = MagicMock()
        mock_collector = MagicMock()
        mock_collector.fetch_historical.return_value = []
        registry.get.return_value = mock_collector
        svc = DataIngestionService(registry=registry)
        mid = uuid.uuid4()
        svc.add_source(mid, "BTCUSD", "mock")
        assert svc.get_latest_close(mid) is None

    def test_get_latest_close_no_data(self):
        registry = MagicMock()
        registry.get.return_value = None
        svc = DataIngestionService(registry=registry)
        svc.add_source(uuid.uuid4(), "BTCUSD", "mock")
        assert svc.get_latest_close(uuid.uuid4()) is None

    def test_fetch_candles_with_source(self):
        registry = MagicMock()
        mock_collector = MagicMock()
        from datetime import datetime

        mock_candle = MagicMock()
        mock_candle.timestamp = datetime(2024, 1, 1, tzinfo=UTC)
        mock_candle.open = 100.0
        mock_candle.high = 101.0
        mock_candle.low = 99.0
        mock_candle.close = 100.5
        mock_candle.volume = 1000.0
        mock_collector.fetch_historical.return_value = [mock_candle]
        registry.get.return_value = mock_collector
        svc = DataIngestionService(registry=registry)
        mid = uuid.uuid4()
        svc.add_source(mid, "BTCUSD", "mock")
        result = svc.fetch_candles(mid, limit=10)
        assert len(result) == 1
        assert float(result[0].ohlcv.close) == 100.5

    def test_fetch_all(self):
        registry = MagicMock()
        mock_collector = MagicMock()
        mock_collector.fetch_latest.return_value = [{"close": 100.0}]
        registry.get_collector.return_value = mock_collector
        svc = DataIngestionService(registry=registry)
        svc.add_source(uuid.uuid4(), "BTCUSD", "mock")
        result = svc.fetch_all(limit=5)
        assert "BTCUSD" in result

    def test_remove_source(self):
        registry = MagicMock()
        svc = DataIngestionService(registry=registry)
        svc.add_source(uuid.uuid4(), "BTCUSD", "mock")
        svc.add_source(uuid.uuid4(), "ETHUSD", "mock")
        svc.remove_source("BTCUSD")
        assert len(svc.sources) == 1


class TestBacktestingServiceSell:
    def test_backtest_sell_signal(self):
        svc = BacktestingService(execution=ExecutionService())
        strategy = MagicMock()

        class FakeResult:
            direction = "short"
            confidence = 0.8
            signal_type = "entry"

        strategy.evaluate.return_value = FakeResult()
        from datetime import UTC
        from datetime import datetime
        from decimal import Decimal

        from traderos.domain.entities import OHLCV
        from traderos.domain.entities import Candle
        from traderos.domain.entities import Timeframe

        mid = uuid.uuid4()
        candles = [
            Candle(
                market_id=mid,
                ohlcv=OHLCV(
                    open=Decimal(100),
                    high=Decimal(101),
                    low=Decimal(99),
                    close=Decimal(100),
                    volume=Decimal(1000),
                ),
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
                timeframe=Timeframe.DAY_1,
            )
            for _ in range(20)
        ]
        _, steps = svc.run(strategy, candles, mid)
        assert steps is not None


class TestDaemonControllerEdgeCases:
    def test_daemon_controller_properties(self):
        from traderos.application.daemon_controller import DaemonController

        executor = MagicMock()
        ctrl = DaemonController(
            mode=TradingMode.PAPER,
            cycle_executor=executor,
            event_bus=MagicMock(),
            health=MagicMock(),
            audit=MagicMock(),
            metrics=MagicMock(),
            notifications=MagicMock(),
            run_manifest=MagicMock(),
        )
        assert ctrl.mode == TradingMode.PAPER
        assert ctrl.running is False
        ctrl.start()
        assert ctrl.running is True
        ctrl.stop()
        assert ctrl.running is False

    def test_daemon_controller_get_status(self):
        from traderos.application.daemon_controller import DaemonController

        ctrl = DaemonController(
            mode=TradingMode.PAPER,
            cycle_executor=MagicMock(),
            event_bus=MagicMock(),
            health=MagicMock(),
            audit=MagicMock(),
            metrics=MagicMock(),
            notifications=MagicMock(),
            run_manifest=MagicMock(),
        )
        ctrl.start()
        status = ctrl.get_status()
        assert status["mode"] == "paper"
        assert status["running"] is True
        ctrl.stop()

    def test_portfolio_close_position(self):
        from traderos.domain.entities.position import Position
        from traderos.domain.services.portfolio_service import PortfolioService
        from traderos.infrastructure.repositories.in_memory import InMemoryPositionRepository
        from traderos.infrastructure.repositories.in_memory import InMemoryTradeRepository

        pos_repo = InMemoryPositionRepository()
        svc = PortfolioService(
            trade_repo=InMemoryTradeRepository(),
            position_repo=pos_repo,
        )
        pos = Position(
            market_id=uuid.uuid4(),
            quantity=10.0,
            entry_price=100.0,
            current_price=110.0,
            pnl=100.0,
        )
        pos_repo.add(pos)
        realized = svc.close_position(pos, 110.0)
        assert realized == 100.0

    def test_portfolio_rebalance_continue(self):
        from traderos.domain.services.portfolio_service import PortfolioService
        from traderos.infrastructure.repositories.in_memory import InMemoryPositionRepository
        from traderos.infrastructure.repositories.in_memory import InMemoryTradeRepository

        svc = PortfolioService(
            trade_repo=InMemoryTradeRepository(),
            position_repo=InMemoryPositionRepository(),
        )
        trades = svc.rebalance(
            target_allocations={uuid.uuid4(): 0.0001},
            cash=5000.0,
            market_prices={},
        )
        assert trades == []

    def test_daemon_controller_market_ids(self):
        from traderos.application.daemon_controller import DaemonController

        mid = uuid.uuid4()
        ctrl = DaemonController(
            mode=TradingMode.PAPER,
            cycle_executor=MagicMock(),
            event_bus=MagicMock(),
            health=MagicMock(),
            audit=MagicMock(),
            metrics=MagicMock(),
            notifications=MagicMock(),
            run_manifest=MagicMock(),
            market_ids=[mid],
        )
        assert ctrl.market_ids == [mid]
        assert ctrl.mode == TradingMode.PAPER

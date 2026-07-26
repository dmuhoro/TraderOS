from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from traderos.application.orchestrator import TradingMode
from traderos.application.orchestrator import TradingOrchestrator
from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.collectors.base import CollectorRegistry
from traderos.domain.collectors.base import CollectorType
from traderos.domain.services.analysis_service import AnalysisService
from traderos.domain.services.backtesting_service import BacktestingService
from traderos.domain.services.data_ingestion_service import DataIngestionService
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.notification_service import NotificationService
from traderos.domain.services.paper_trading_service import PaperBrokerAdapter
from traderos.domain.services.paper_trading_service import PaperTradingService
from traderos.domain.services.portfolio_service import PortfolioService
from traderos.domain.services.risk_service import RiskService
from traderos.domain.services.signal_service import SignalService
from traderos.infrastructure.audit import AuditService
from traderos.infrastructure.collectors.mock_collector import MockDataCollector
from traderos.infrastructure.config.config_loader import Config
from traderos.infrastructure.events import InMemoryEventBus
from traderos.infrastructure.health import HealthService
from traderos.infrastructure.metrics import MetricsService
from traderos.infrastructure.repositories.in_memory import InMemoryPositionRepository
from traderos.infrastructure.repositories.in_memory import InMemorySignalRepository
from traderos.infrastructure.repositories.in_memory import InMemoryTradeRepository
from traderos.infrastructure.repositories.sqlite import SQLitePositionRepository
from traderos.infrastructure.repositories.sqlite import SQLiteSignalRepository
from traderos.infrastructure.repositories.sqlite import SQLiteTradeRepository
from traderos.infrastructure.run_manifest import RunManifestService


def build_orchestrator(
    mode: str = "paper",
    market_ids: list[uuid.UUID] | None = None,
    config: Config | None = None,
) -> TradingOrchestrator:
    cfg = config or Config.load()
    db = _get_db(cfg.db_path) if cfg.db_path and cfg.db_path != ":memory:" else None

    if db is not None:
        signal_repo = SQLiteSignalRepository(db)
        trade_repo = SQLiteTradeRepository(db)
        pos_repo = SQLitePositionRepository(db)
    else:
        signal_repo = InMemorySignalRepository()
        trade_repo = InMemoryTradeRepository()
        pos_repo = InMemoryPositionRepository()

    signal_service = SignalService(repo=signal_repo)
    risk_service = RiskService()
    portfolio_service = PortfolioService(trade_repo=trade_repo, position_repo=pos_repo)
    execution = ExecutionService()
    analysis = AnalysisService()
    event_bus = InMemoryEventBus()
    health = HealthService()
    audit = AuditService()
    metrics = MetricsService()
    notifications = NotificationService()
    run_manifest = RunManifestService()

    trading_mode = TradingMode(mode)

    # --- Data Ingestion ---
    collector_registry = CollectorRegistry()
    collector_registry.register(MockDataCollector())
    try:
        from traderos.infrastructure.collectors.binance_collector import BinanceCollector

        collector_registry.register(BinanceCollector())
    except ImportError:
        pass

    data_ingestion = DataIngestionService(registry=collector_registry)

    symbols: list[str] = []
    forex = cfg.get("data_collection.forex_symbols", [])
    crypto = cfg.get("data_collection.crypto_symbols", [])
    if isinstance(forex, list):
        symbols.extend(forex)
    if isinstance(crypto, list):
        symbols.extend(crypto)

    data_market_ids: list[uuid.UUID] = []
    for symbol in symbols:
        mid = uuid.uuid5(uuid.NAMESPACE_DNS, f"traderos/{symbol}")
        data_ingestion.add_source(mid, symbol, CollectorType.MOCK)
        data_market_ids.append(mid)

    # --- Broker Selection ---
    broker: BrokerAdapter
    if trading_mode == TradingMode.LIVE and cfg.alpaca_api_key and cfg.alpaca_secret_key:
        try:
            from traderos.infrastructure.alpaca_broker import AlpacaBrokerAdapter

            broker = AlpacaBrokerAdapter(
                api_key=cfg.alpaca_api_key,
                secret_key=cfg.alpaca_secret_key,
                paper=cfg.alpaca_paper,
            )
        except ImportError:
            broker = PaperBrokerAdapter(fill_probability=1.0)
        except (ValueError, RuntimeError, OSError):
            broker = PaperBrokerAdapter(fill_probability=1.0)
    else:
        broker = PaperBrokerAdapter(fill_probability=1.0)

    paper: PaperTradingService | None = None
    if trading_mode == TradingMode.PAPER:
        paper = PaperTradingService(
            broker=broker,
            signal_service=signal_service,
            risk_service=risk_service,
            portfolio_service=portfolio_service,
            execution=execution,
        )

    backtest = BacktestingService(execution=execution)

    if market_ids is not None:
        mids = market_ids
    elif data_market_ids:
        mids = data_market_ids
    else:
        mids = [uuid.uuid4()]

    orch = TradingOrchestrator(
        mode=trading_mode,
        signal_service=signal_service,
        risk_service=risk_service,
        portfolio_service=portfolio_service,
        execution=execution,
        analysis=analysis,
        broker=broker,
        backtest=backtest,
        paper=paper,
        data_ingestion=data_ingestion,
        event_bus=event_bus,
        health=health,
        audit=audit,
        metrics=metrics,
        notifications=notifications,
        run_manifest=run_manifest,
        market_ids=mids,
    )
    return orch


def _get_db(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn

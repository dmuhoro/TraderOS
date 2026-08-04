from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from traderos.application.orchestrator import TradingMode
from traderos.application.orchestrator import TradingOrchestrator
from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.collectors.base import CollectorRegistry
from traderos.domain.collectors.base import CollectorType
from traderos.domain.exceptions import InfrastructureError
from traderos.domain.ports import AuditPort
from traderos.domain.services.analysis_service import AnalysisService
from traderos.domain.services.backtesting_service import BacktestingService
from traderos.domain.services.broker_state_reconciliation_service import (
    BrokerStateReconciliationService,
)
from traderos.domain.services.data_ingestion_service import DataIngestionService
from traderos.domain.services.execution_service import ExecutionService
from traderos.domain.services.flatten_service import FlattenService
from traderos.domain.services.knowledge_graph_service import KnowledgeGraphService
from traderos.domain.services.live_readiness import LiveReadinessService
from traderos.domain.services.market_hours_engine import MarketHoursEngine
from traderos.domain.services.notification_service import NotificationService
from traderos.domain.services.operator_session import OperatorSessionService
from traderos.domain.services.operator_workflow import OperatorWorkflow
from traderos.domain.services.paper_trading_service import PaperBrokerAdapter
from traderos.domain.services.paper_trading_service import PaperTradingService
from traderos.domain.services.portfolio_service import PortfolioService
from traderos.domain.services.preflight_service import PreflightService
from traderos.domain.services.reconciliation_service import OrderReconciliationService
from traderos.domain.services.reconciliation_service import PersistentKillSwitch
from traderos.domain.services.research_service import ResearchService
from traderos.domain.services.risk_service import RiskService
from traderos.domain.services.signal_service import SignalService
from traderos.domain.services.strategy_framework import registry as strategy_registry
from traderos.domain.services.strategy_management import StrategyCatalogService
from traderos.infrastructure.audit import AuditService as InMemoryAuditService
from traderos.infrastructure.collectors.mock_collector import MockDataCollector
from traderos.infrastructure.config.config_loader import Config
from traderos.infrastructure.database.connection import get_connection
from traderos.infrastructure.database.connection import resolve_backend
from traderos.infrastructure.database.migration_manager import migrate
from traderos.infrastructure.events import InMemoryEventBus
from traderos.infrastructure.ha_failover import FailoverManager
from traderos.infrastructure.ha_failover import LeaseStore
from traderos.infrastructure.health import HealthService as InMemoryHealthService
from traderos.infrastructure.metrics import MetricsService as InMemoryMetricsService
from traderos.infrastructure.notifiers.webhook_notifier import WebhookNotifier
from traderos.infrastructure.observability import SQLiteAuditService
from traderos.infrastructure.observability import SQLiteHealthService
from traderos.infrastructure.observability import SQLiteManifestService
from traderos.infrastructure.observability import SQLiteMetricsService
from traderos.infrastructure.observability_postgres import PostgresAuditService
from traderos.infrastructure.observability_postgres import PostgresHealthService
from traderos.infrastructure.observability_postgres import PostgresManifestService
from traderos.infrastructure.observability_postgres import PostgresMetricsService
from traderos.infrastructure.repositories.in_memory import InMemoryBacktestResultRepository
from traderos.infrastructure.repositories.in_memory import InMemoryExperimentRepository
from traderos.infrastructure.repositories.in_memory import InMemoryExperimentResultRepository
from traderos.infrastructure.repositories.in_memory import InMemoryHypothesisRepository
from traderos.infrastructure.repositories.in_memory import InMemoryKnowledgeEdgeRepository
from traderos.infrastructure.repositories.in_memory import InMemoryKnowledgeNodeRepository
from traderos.infrastructure.repositories.in_memory import InMemoryLessonRepository
from traderos.infrastructure.repositories.in_memory import InMemoryObservationRepository
from traderos.infrastructure.repositories.in_memory import InMemoryOperatorWorkflowRepository
from traderos.infrastructure.repositories.in_memory import InMemoryPositionRepository
from traderos.infrastructure.repositories.in_memory import InMemorySignalRepository
from traderos.infrastructure.repositories.in_memory import InMemoryStrategyRepository
from traderos.infrastructure.repositories.in_memory import InMemoryTradeRepository
from traderos.infrastructure.repositories.postgres import PostgresPositionRepository
from traderos.infrastructure.repositories.postgres import PostgresSignalRepository
from traderos.infrastructure.repositories.postgres import PostgresTradeRepository
from traderos.infrastructure.repositories.sqlite import SQLiteOperatorWorkflowRepository
from traderos.infrastructure.repositories.sqlite import SQLitePositionRepository
from traderos.infrastructure.repositories.sqlite import SQLiteSignalRepository
from traderos.infrastructure.repositories.sqlite import SQLiteStrategyRepository
from traderos.infrastructure.repositories.sqlite import SQLiteTradeRepository
from traderos.infrastructure.run_manifest import RunManifestService as InMemoryManifestService
from traderos.infrastructure.secrets import EnvSecretProvider
from traderos.infrastructure.secrets import SecretRotator
from traderos.infrastructure.supervision import JsonlHeartbeatStore
from traderos.infrastructure.supervision import SupervisionService

PG_BACKEND = "postgres"


def build_orchestrator(
    mode: str = "paper",
    market_ids: list[uuid.UUID] | None = None,
    config: Config | None = None,
) -> TradingOrchestrator:
    cfg = config or Config.load()
    backend = resolve_backend(cfg.database_url)
    db = _get_db(cfg, backend)

    if db is not None:
        if backend == PG_BACKEND:
            signal_repo = PostgresSignalRepository(db)
            trade_repo = PostgresTradeRepository(db)
            pos_repo = PostgresPositionRepository(db)
        else:
            signal_repo = SQLiteSignalRepository(db)
            trade_repo = SQLiteTradeRepository(db)
            pos_repo = SQLitePositionRepository(db)
    else:
        signal_repo = InMemorySignalRepository()
        trade_repo = InMemoryTradeRepository()
        pos_repo = InMemoryPositionRepository()

    signal_service = SignalService(repo=signal_repo)
    persistent_kill_switch = PersistentKillSwitch()
    portfolio_service = PortfolioService(trade_repo=trade_repo, position_repo=pos_repo)
    execution = ExecutionService()
    analysis = AnalysisService()
    event_bus = InMemoryEventBus()
    if db is not None:
        if backend == PG_BACKEND:
            health = PostgresHealthService(db)
            audit = PostgresAuditService(db)
            metrics = PostgresMetricsService(db)
            run_manifest = PostgresManifestService(db)
        else:
            health = SQLiteHealthService(db)
            audit = SQLiteAuditService(db)
            metrics = SQLiteMetricsService(db)
            run_manifest = SQLiteManifestService(db)
    else:
        health = InMemoryHealthService()
        audit = InMemoryAuditService()
        metrics = InMemoryMetricsService()
        run_manifest = InMemoryManifestService()
    risk_service = RiskService(
        persistent_kill_switch=persistent_kill_switch,
        metrics=metrics,
        audit=audit,
        max_gross_exposure=float(cfg.get("risk.max_gross_exposure", 1.0)),
        max_data_staleness_seconds=float(cfg.get("risk.max_data_staleness_seconds", 300.0)),
        allowed_markets=_resolve_allowed_markets(cfg),
    )
    portfolio_service.risk_service = risk_service
    webhook_notifier = WebhookNotifier()
    notifications = NotificationService(notifier=webhook_notifier)

    _sync_strategy_registry(db, backend)

    trading_mode = TradingMode(mode)

    # --- Data Ingestion ---
    collector_registry = CollectorRegistry()
    collector_registry.register(MockDataCollector())
    try:
        from traderos.infrastructure.collectors.binance_collector import BinanceCollector

        collector_registry.register(BinanceCollector())
    except ImportError:  # pragma: no cover
        pass

    data_ingestion = DataIngestionService(registry=collector_registry)
    market_hours = MarketHoursEngine()
    reconciliation = OrderReconciliationService()

    symbols: list[str] = []
    forex = cfg.get("data_collection.forex_symbols", [])
    crypto = cfg.get("data_collection.crypto_symbols", [])
    if isinstance(forex, list):
        symbols.extend(forex)
    if isinstance(crypto, list):
        symbols.extend(crypto)

    # WP-4: a real market-data feed is used only when explicitly enabled via
    # config (data_collection.binance.enabled) AND the collector is available.
    # Defaults to the deterministic mock collector so CI/tests never touch the
    # network (Constitution §2 Principle 6: Test Before Trust).
    binance_enabled = bool(cfg.get("data_collection.binance.enabled", False))
    crypto_collector_type = (
        CollectorType.BINANCE
        if binance_enabled and collector_registry.get(CollectorType.BINANCE) is not None
        else CollectorType.MOCK
    )

    symbol_map: dict[uuid.UUID, str] = {}
    data_market_ids: list[uuid.UUID] = []
    for symbol in symbols:
        mid = uuid.uuid5(uuid.NAMESPACE_DNS, f"traderos/{symbol}")
        source_type = crypto_collector_type if symbol in crypto else CollectorType.MOCK
        data_ingestion.add_source(mid, symbol, source_type)
        symbol_map[mid] = symbol
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
                symbol_map=symbol_map,
            )
        except ImportError:  # pragma: no cover
            broker = PaperBrokerAdapter(fill_probability=1.0)
        except (ValueError, RuntimeError, OSError, InfrastructureError):  # pragma: no cover
            broker = PaperBrokerAdapter(fill_probability=1.0)
    else:
        broker = PaperBrokerAdapter(fill_probability=1.0)

    from traderos.infrastructure.broker_rate_limiter import RateLimitedBroker
    from traderos.infrastructure.order_guardrail import GuardrailedBroker

    broker = GuardrailedBroker(RateLimitedBroker(broker))

    # CLOSURE-12: give the live broker a durable, restart-safe idempotency
    # journal. Every order intent is persisted before submission so a crashed
    # submit is never double-fired and broker-side truth can be reconciled
    # from the journal (single source of truth for "what we tried to do").
    _journaled: Any | None = None
    if trading_mode == TradingMode.LIVE and cfg.db_path:
        try:
            import sqlite3

            from traderos.infrastructure.journal import OrderEventJournal
            from traderos.infrastructure.journaled_broker import JournaledBroker

            _raw = sqlite3.connect(str(cfg.db_path), check_same_thread=False)
            _journaled = JournaledBroker(broker, OrderEventJournal(_raw))
        except Exception:  # noqa: BLE001 — journaling is best-effort, never fatal
            _journaled = None
        if _journaled is not None:
            broker = _journaled

    broker_reconciliation = BrokerStateReconciliationService(broker=broker)

    preflight_service = PreflightService(
        audit=audit,
        broker_reconciliation=broker_reconciliation,
        kill_switch=risk_service.kill_switch,
        allowed_markets=risk_service.allowed_markets,
        require_allowlist=bool(cfg.get("risk.require_allowlist", False)),
    )

    paper: PaperTradingService | None = None
    if trading_mode in (TradingMode.PAPER, TradingMode.LIVE):
        paper = PaperTradingService(
            broker=broker,
            signal_service=signal_service,
            risk_service=risk_service,
            portfolio_service=portfolio_service,
            execution=execution,
        )

    backtest = BacktestingService(execution=execution)

    knowledge_graph = KnowledgeGraphService(
        nodes=InMemoryKnowledgeNodeRepository(),
        edges=InMemoryKnowledgeEdgeRepository(),
    )
    research = ResearchService(
        observations=InMemoryObservationRepository(),
        hypotheses=InMemoryHypothesisRepository(),
        experiments=InMemoryExperimentRepository(),
        results=InMemoryExperimentResultRepository(),
        lessons=InMemoryLessonRepository(),
    )

    if db is not None and backend != PG_BACKEND:
        strategy_repo = SQLiteStrategyRepository(db)
        workflow_repo = SQLiteOperatorWorkflowRepository(db)
    else:
        # PostgreSQL strategy/workflow repos do not exist yet (WP-C4 scope);
        # the operator catalog degrades to in-memory on PG-backed builds.
        strategy_repo = InMemoryStrategyRepository()
        workflow_repo = InMemoryOperatorWorkflowRepository()

    strategy_catalog = StrategyCatalogService(
        repo=strategy_repo,
        backtest=backtest,
        backtest_results=(
            InMemoryBacktestResultRepository()
            if db is None
            else None  # legacy v001 backtest_results schema diverges (SQLite)
        ),
    )
    strategy_catalog.ensure_seeded()

    operator_workflow = workflow_repo.load()
    if operator_workflow is None:
        operator_workflow = OperatorWorkflow()
        workflow_repo.save(operator_workflow)

    operator_session = OperatorSessionService(
        workflow=operator_workflow,
        repository=workflow_repo,
        preflight=preflight_service,
        broker=broker,
        broker_reconciliation=broker_reconciliation,
        data_ingestion=data_ingestion,
        paper=paper,
        strategy_catalog=strategy_catalog,
        live_mode=trading_mode == TradingMode.LIVE,
    )

    live_readiness = LiveReadinessService(
        broker=broker,
        data_ingestion=data_ingestion,
        preflight=preflight_service,
        kill_switch=risk_service.kill_switch,
        operator_session=operator_session,
        live_execution_enabled=trading_mode == TradingMode.LIVE,
    )

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
        market_hours=market_hours,
        reconciliation=reconciliation,
        broker_reconciliation=broker_reconciliation,
        preflight_service=preflight_service,
        event_bus=event_bus,
        health=health,
        audit=audit,
        metrics=metrics,
        notifications=notifications,
        run_manifest=run_manifest,
        market_ids=mids,
        strategy_repository=strategy_repo,
        workflow_repository=workflow_repo,
        operator_workflow=operator_workflow,
        strategy_catalog=strategy_catalog,
        operator_session=operator_session,
        live_readiness=live_readiness,
        secret_rotator=_build_secret_rotator(),
        knowledge_graph=knowledge_graph,
        research=research,
        flatten_service=FlattenService(
            broker=broker,
            portfolio_service=portfolio_service,
            notifications=notifications,
            audit=audit,
            metrics=metrics,
            market_prices=lambda mid: (data_ingestion.get_latest_close(mid) or 0.0),
        ),
        supervision=SupervisionService(
            store=JsonlHeartbeatStore(Path(cfg.data_dir) / "supervision.jsonl"),
            notifications=notifications,
            audit=audit,
            metrics=metrics,
        ),
        failover=_build_failover(cfg, notifications, audit),
    )
    return orch


def _build_failover(
    cfg: Config,
    notifications: NotificationService,
    audit: AuditPort | None,
) -> FailoverManager | None:
    """Build the HA failover manager when ``ha.enabled`` is set. A standby
    daemon starts only when it wins the durable lease, so a crashed primary is
    replaced without a split brain (G-04)."""
    if not bool(cfg.get("ha.enabled", False)):
        return None
    return FailoverManager(
        store=LeaseStore(Path(cfg.data_dir) / "ha_lease.jsonl"),
        notifications=notifications,
        audit=audit,
        stale_after_seconds=float(cfg.get("ha.lease_stale_after_seconds", 90.0)),
    )


def _resolve_allowed_markets(cfg: Config) -> frozenset[uuid.UUID]:
    """Resolve ``risk.allowed_markets`` (symbol strings) to market ids.

    Uses the same deterministic ``uuid5("traderos/{symbol}")`` scheme as the
    data-ingestion wiring, so an allowlisted symbol maps to the market the
    loop trades. Empty list = unrestricted (unless ``risk.require_allowlist``
    forces one via preflight in live mode).
    """
    symbols = cfg.get("risk.allowed_markets", [])
    if not isinstance(symbols, list):
        return frozenset()
    return frozenset(
        uuid.uuid5(uuid.NAMESPACE_DNS, f"traderos/{s}") for s in symbols if isinstance(s, str)
    )


def _build_secret_rotator() -> SecretRotator:
    rotator = SecretRotator()
    rotator.add_provider(EnvSecretProvider())
    return rotator


def _sync_strategy_registry(db: Any | None, backend: str = "sqlite") -> None:
    if db is None:
        return
    if backend == PG_BACKEND:
        with db.cursor() as cur:
            cur.execute("SELECT name FROM strategy_registry WHERE status = 'active'")
            persisted = {row[0] for row in cur.fetchall()}
        for name in strategy_registry.list():
            if name not in persisted:
                with db.cursor() as cur:
                    cur.execute(
                        "INSERT INTO strategy_registry (name, params, version, status) "
                        "VALUES (%s, %s, %s, %s) ON CONFLICT (name) DO NOTHING",
                        (name, "{}", "1.0.0", "active"),
                    )
        db.commit()
    else:
        cur = db.execute("SELECT name FROM strategy_registry WHERE status = 'active'")
        persisted = {row["name"] for row in cur.fetchall()}
        for name in strategy_registry.list():
            if name not in persisted:
                db.execute(
                    "INSERT OR IGNORE INTO strategy_registry (name, params, version, status) "
                    "VALUES (?, ?, ?, ?)",
                    (name, "{}", "1.0.0", "active"),
                )
        db.commit()


def _get_db(config: Config, backend: str, retention_days: int = 90) -> Any | None:
    use_in_memory = not config.database_url and (not config.db_path or config.db_path == ":memory:")
    if use_in_memory:
        return None
    conn = get_connection(config)
    migrate(conn)
    from traderos.infrastructure.archiver import purge_old_entries

    purge_old_entries(conn, retention_days)
    return conn

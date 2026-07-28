# Changelog - TraderOS

## [Unreleased] - Sprint 9

### Added
- Provider-neutral streaming market data pipeline with bounded backpressure, heartbeat, latency, clock-drift observation, reconnect handling, candle aggregation and replay recording.
- Enriched event context and deterministic idempotent order-event engine.
- Alpaca health/error classification, account synchronization, buying-power verification and order modification support.
- Sprint 9 tests, benchmark, architecture documentation and live market infrastructure report.

### WP-7.1 — Architecture Fitness & Risk-Path Integrity
- **ADR-007 ratified**: Manual-reset-only circuit breaker replaces cooldown-based auto-reset. `can_trade()` returns `False` unconditionally while circuit is open; only explicit `reset()` clears the breaker. Preserves failure evidence for post-mortem per Constitution §2 Principle 2.
- **Dependency direction enforcement**: `tests/architecture/test_dependency_direction.py` uses AST walk to verify domain/ never imports from infrastructure/. Catches violations at CI time. Includes a deliberately-broken fixture to prove the check can fail.
- **NotifierPort protocol** (`domain/ports.py`): Port for out-of-band notification delivery (webhook, Slack, etc.). Domain services now depend only on the protocol.
- **WebhookNotifier adapter** (`infrastructure/notifiers/webhook_notifier.py`): Extracts webhook POST logic (with retry) from notification_service.py into the infrastructure layer where it belongs.
- **Dependency rule restored**: `notification_service.py` no longer imports `retry_with_backoff` from `infrastructure.retry`. Webhook delivery delegates to injected `NotifierPort`.
- **KillSwitch metrics**: `RiskService` accepts optional `MetricsPort`; kill-switch trips increment the `kill_switch_trips` counter for operational visibility.
- **Manual-reset-only circuit breaker**: `KillSwitch` and `PersistentKillSwitch` both enforce manual-reset semantics. Removed dead `circuit_open_until` field and cooldown-based auto-reset logic from `PersistentKillSwitch`.
- **Coverage threshold**: `pyproject.toml` `fail_under = 70` documented as MEP §17 interim gate with path to 90%.
- **736 tests passing.**

## [1.1.0] - 2026-07-28

### Added
- **Production Readiness Programme (Sprint 7):** Complete production hardening across 6 phases.

### Phase 1 — Production Blockers (6 items)
- **HTTPS:** `SSL_KEYFILE`/`SSL_CERTFILE` env vars wired to uvicorn in `main.py`.
- **Secure CORS:** `CORS_ORIGINS` env var (comma-separated); defaults to `*` for local dev.
- **CI security gates:** Removed `|| true` from `pip-audit` and `bandit` steps.
- **Domain exception adoption:** Replaced `RuntimeError`/`ValueError` with `ServiceError`/`InfrastructureError`/`ConfigError` in `retry.py`, `alpaca_broker.py`, `config_loader.py`, `notification_service.py`.
- **Startup validation:** `validate` CLI command; daemon calls `Config.validate()` before run loop.
- **Dependency hygiene:** Stale `requirements.txt` deleted; `pyproject.toml` is sole source of truth.

### Phase 2 — PostgreSQL Production Database
- `DATABASE_URL` env var for runtime database backend selection.
- `psycopg2-binary` as optional `postgres` dependency.
- Database connection factory (`connection.py`) returns `sqlite3.Connection` or psycopg2 connection.
- DB-agnostic migrations: all 3 migrations accept `backend="sqlite"` param, emit appropriate DDL (`SERIAL` vs `AUTOINCREMENT`, `ON CONFLICT` vs `INSERT OR IGNORE`).
- `PostgresRepository[T]` base class mirroring `SQLiteRepository[T]` with `%s` placeholders.
- PostgreSQL observability services: `PostgresAuditService`, `PostgresMetricsService`, `PostgresHealthService`, `PostgresManifestService`.
- Factory dispatches to Postgres repos/services when `DATABASE_URL` is set.

### Phase 3 — Observability
- `prometheus-client` as optional `monitoring` dependency.
- `PrometheusMetricsService` wrapping `prometheus_client.Counter`/`Gauge`/`Histogram`.
- Prometheus `/metrics` scrape endpoint (standard exposition format).
- Structured JSON logging via `JsonFormatter` + `setup_json_logging()`.
- HTTP request metrics middleware (counters + duration histograms).

### Phase 4 — API Hardening
- In-memory sliding-window rate limiter (`RateLimiter`) with `RATE_LIMIT_MAX` env var.
- Rate limiting middleware returns 429 + `X-RateLimit-Remaining` header.
- `/metrics` endpoint exempted from API key auth (Prometheus scraping standard).

### Phase 5 — Deployment
- Dockerfile updated to Python 3.14-slim with all extras (`api`, `alpaca`, `postgres`, `monitoring`).
- `railway.json` for Railway deployment with health check path.
- `nixpacks.toml` as alternative build config.
- CI pipeline upgraded to Python 3.14 with all extras.

### Phase 6 — Verification
- PrometheusMetricsService unit tests (counter, gauge, snapshot, timing).
- RateLimiter unit tests (within-limit, over-limit, remaining, key isolation).
- Database connection tests (backend resolution, ImportError for missing psycopg2).
- API integration tests for `/metrics` endpoint and rate limit headers.
- **666 tests passing at 86% coverage.**

### New files
- `src/traderos/infrastructure/database/connection.py`
- `src/traderos/infrastructure/monitoring.py`
- `src/traderos/infrastructure/rate_limiter.py`
- `src/traderos/infrastructure/observability_postgres.py`
- `src/traderos/infrastructure/repositories/postgres/`
- `railway.json`, `nixpacks.toml`
- `tests/test_monitoring.py`, `tests/test_rate_limiter.py`, `tests/test_database_connection.py`

## [1.0.0] - 2026-07-27

### Added
- **Post-merge Polish (Phases 0-3):** `assert`→`RuntimeError` in production code, version unification, MIT LICENSE, `.env.example`. Full README + CONTRIBUTING docs. Deleted 5 unused CLI/visualization files. CI/CD with pip-audit + bandit security job, Docker build/push to GHCR.
- **Coverage Layers (A-E):** `db_manager.py` (48%→89%), `observability.py` (63%→99%), `binance_collector.py` (50%→93%), `cycle_executor.py` (63%→76%), `daemon_controller.py` (63%→94%).
- **API v1 polish:** All routes grouped under `/v1/` prefix via `APIRouter`. Consistent error envelope `{"error": {"code": N, "message": "..."}}`. Request logging middleware (method, path, status, duration).
- 622 tests passing at 89% coverage.

### Changed
- **API routes now under `/v1/`:** `/v1/health`, `/v1/strategies`, `/v1/strategies/{name}`, `/v1/backtest`, `/v1/orchestrator/start`, `/v1/orchestrator/stop`, `/v1/orchestrator/status`, `/v1/papertrade/session`, `/v1/papertrade/sessions`, `/v1/audit`, `/v1/metrics`, `/v1/manifest`.
- **Error format:** 40x and 50x errors now return `{"error": {"code": N, "message": "..."}}` instead of `{"detail": "..."}`.

## [0.8.0] - v1 Readiness: Architecture Hardening

### Added
- **Domain port protocols:** `EventBusPort`, `HealthPort`, `AuditPort`, `MetricsPort`, `ManifestPort` defined in `domain/ports.py` with structural typing. Application layer now depends on protocols instead of concrete infrastructure.
- **SPRINT_6.md** documents the v1 readiness sprint plan.
- **Layer 10 — Production Hardening:**
  - **Retry with backoff:** `infrastructure/retry.py` — exponential backoff with jitter, max 3 attempts, applied to Alpaca broker order submission and notification webhook.
  - **Data archival:** `infrastructure/archiver.py` — `purge_old_entries()` deletes rows older than 90 days from 5 SQLite tables (audit_log, metrics_history, health_history, trades, strategy_registry).
  - **Strategy registry persistence:** `v003_strategies.py` migration creates `strategy_registry` table with 3 built-in seed strategies; `_sync_strategy_registry()` syncs in-memory registry to SQLite on startup.
  - **Config validation improvements:** Validates db_path directory exists, MAX_DRAWDOWN is 0-100 (not just >100), data_collection.forex_symbols is a list.
  - **Auto-purge on startup:** `_get_db()` calls `purge_old_entries()` after migrations.

### Changed
- **`Event` dataclass moved to `domain/ports.py`:** Shared value object used by both domain protocols and infrastructure implementations.
- **`InMemoryEventBus` implements `EventBusPort`** protocol (was separate ABC in infrastructure).
- **`HealthService` implements `HealthPort`** protocol, uses `HealthStatus` port type.
- **`AuditService` implements `AuditPort`** protocol, uses `AuditEntry` port type.
- **`MetricsService` implements `MetricsPort`** protocol, uses `MetricSample` port type.
- **`RunManifestService` implements `ManifestPort`** protocol, uses `ManifestEntry` port type.
- **`TradingOrchestrator` depends on port protocols** instead of concrete infrastructure classes.
- **Factory imports concrete implementations** as composition root; wire via protocol types.

## [0.7.0] - Sprint Finale: 15 Quick Wins
### Added
- **`--json` output flag on CLI:** `strategies`, `health`, `audit`, `signal` commands output structured JSON when `--json` is passed (GAP-19).
- **Health check in Dockerfile:** `HEALTHCHECK CMD traderos health || exit 1` enables Docker orchestration health monitoring (GAP-16).
- **CORS middleware in API:** FastAPI server allows cross-origin requests via `CORSMiddleware(allow_origins=["*"])` (GAP-17).
- **`/papertrade/session` market_ids body:** Accepts `CreatePaperSessionRequest` with optional `market_ids`; falls back to `settings.yaml` symbols (GAP-11).
- **Limit order support in Alpaca adapter:** `place_limit_order()` calls `trading_client.submit_order()` with `LimitOrderRequest` (GAP-21).
- **Notification persistence + webhook:** `_send_file()` writes JSONL to `logs/notifications.jsonl`; `_send_webhook()` POSTs to `$WEBHOOK_URL` (GAP-9).

### Fixed
- **`Config.load()` ignores `settings.yaml` nested keys:** 8 fields now translate from `settings.yaml` dotted keys (`database.path`→`db_path`, `logging.level`→`log_level`, etc.) (GAP-14).
- **Docker `MODE=paper` env var has no effect:** Removed `MODE` from both service definitions in `docker-compose.yml` (GAP-15).
- **Paper broker balance still hardcoded in `/v1/papertrade`:** `account_balance` reads `DEFAULT_CASH` env var with `10000.0` fallback (GAP-25).
- **`/metrics` panics when orchestrator not running:** Returns `{"warning": "Orchestrator not started"}`, removing 500 error (GAP-27).
- **Backtest can hang forever:** `BacktestingService.run()` accepts `max_duration_seconds=300` and raises `TimeoutError` with remaining-candle count (GAP-20).
- **Daemon can hang on shutdown:** `Orchestrator.run_forever()` forces exit after `shutdown_timeout=30` seconds (GAP-23).
- **EventBus handler crash kills broker:** `InMemoryEventBus.publish()` wraps each handler in try/except, logging and isolating exceptions (GAP-22).

### Removed
- **Strategy lab `run` subparser:** `strategy_lab.py` stripped to just `list` command; obsolete strategy-lab run path removed (GAP-7).

## [0.6.1] - Stale Module Cleanup & CLI Wiring
### Fixed
- **Hardcoded `close_price=100.0` in `run_forever()`:** Now fetches real data via `data_ingestion.get_latest_close()`; skips cycle and reports unhealthy when price unavailable instead of silently trading at $100.
- **5 stale domain module groups deleted:** `liquidity/` (5 files), `analysis/indicators.py`, `risk/engine.py`, `strategies/` (base_strategy.py + strategies.py), `backtesting/engine.py`. Test files updated to remove references; `strategy_lab.py` updated to use new `strategy_framework` registry.
- **`cmd_signal` no-op in CLI:** Now builds an orchestrator and displays active signals from `SignalService.get_active_signals()`. Market-specific or all-markets listing.
- **Paper trading cash hardcoded in 6 locations:** Consolidated to `Config.default_cash` field with `DEFAULT_CASH` env var fallback; `PaperBrokerAdapter.account_balance`, `PaperSession.initial_capital`/`current_capital`, `BacktestingService.initial_capital`, `TradingOrchestrator._cash_balance()` all read from config.
- **3 `assert` statements in production code:** `server.py:78`, `migration_manager.py:55`, `research_engine.py:67` replaced with proper `RuntimeError` + error context.
- **Version inconsistency (`0.2.0` vs `0.4.0` vs `0.6.0`):** `pyproject.toml` version set to `0.6.0`; server and CLI now read `importlib.metadata.version("traderos")` instead of hardcoded strings.
- **Stale `.env.example`:** Replaced with template including `DEFAULT_CASH`, `MODE`, `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_PAPER`.

## [0.6.0] - Infrastructure Hardening & Pipeline Wiring
### Fixed
- **Container runs as root:** Added `USER traderos` directive to Dockerfile with dedicated system user/group.
- **SQLite concurrent access:** Enabled WAL journal mode + `busy_timeout=5000` + connection `timeout=10` in `DatabaseManager` for safe multi-container operation.
- **DB connections leaked:** Added `__enter__`/`__exit__` context manager to `DatabaseManager` for guaranteed cleanup.
- **All dependency versions unpinned:** `pyproject.toml` dependencies now use `==` pinning; 10 undeclared dependencies (numpy, pandas, PyYAML, python-dotenv, matplotlib, seaborn, tabulate, pydantic, fastapi, uvicorn, alpaca-py) added with pinned versions. `dev` optional-dependency group added for tooling.
- **Alpaca UUID→symbol bug:** `AlpacaBrokerAdapter` accepts `symbol_map` dict; factory builds mapping from `DataIngestionService` sources and passes it at construction. Runtime symbol resolution replaces broken `str(market_id)`.
- **AnalysisService dead code:** `TradingOrchestrator.run_cycle()` now fetches real candle data via `data_ingestion.fetch_candles()` and computes SMA/ATR via `AnalysisService` static methods. Fake indicators (`close*1.01`, `close*0.99`, `volume=1000`) replaced with computed values.
- **Dual collector implementations:** Removed old `infrastructure/data/collectors.py` and `infrastructure/data/pipeline.py`; single `infrastructure/collectors/` hierarchy (DataCollector ABC) is the sole data collector path. Eliminates `ccxt` and `yfinance` import dependencies.
- **`fail_under` raised to 70:** Previous sprint set to 70 (was 30); now 514 tests pass at 76% coverage.

## [0.5.0] - Blocker Clearance & Architecture Cleanup
### Fixed
- **Docker build broken:** `.dockerignore` no longer excludes `pyproject.toml`; build succeeds again.
- **`fail_under = 30`:** Raised to 70 to prevent coverage regression masking.
- **`Config.load()` `or` truthiness bug:** Falsy env vars (`""`, `"0"`) no longer silently skipped to YAML defaults.
- **`Config.validate()` dead code:** Now called at end of `Config.load()`.
- **3 competing DB path defaults:** Consolidated to `config.db_path` as single canonical source.
- **Slippage direction bug:** `PaperBrokerAdapter` now uses `1 - bps/10000` for sells (was always `1 + bps`, giving sells better-than-market price).
- **Backtest equity bug:** `BacktestingService.run()` tracks cash separately from position value; equity = cash + position_qty × close (was using constant initial_capital, producing phantom profits).
- **Old signals re-processed:** `TradingOrchestrator.run_cycle()` processes only the newly generated signal instead of all active signals.
- **`FillResult` name collision:** `execution_service.FillResult` renamed to `ExecutionFillResult` (different `status` types: `str` vs `OrderStatus`).
- **`assert` in alpaca_broker.py:** Replaced with proper conditional checks (assert disabled by `-O` flag).
- **`assert` in research_engine.py:** 4 instances replaced with `if cursor.lastrowid is None: raise RuntimeError(...)`.
- **Hardcoded $10,000 cash:** `TradingOrchestrator` uses `_cash_balance()` which returns broker balance in LIVE mode, configurable default otherwise.

### Added
- **CI pipeline:** `.github/workflows/ci.yml` — 4-job pipeline (lint → typecheck → test → docker) with concurrency grouping and caching.
- **`DatabasePort` protocol:** `domain/ports.py` breaks dependency rule violation; 5 domain classes no longer import `DatabaseManager` from infrastructure.
- **Missing `__init__.py`:** `infrastructure/logging/`, `infrastructure/repositories/` now have proper package init files.
- **SPRINT_5.md** documents the blocker clearance sprint.

### Removed
- **10 stale flat module directories:** `analysis_engine/`, `backtesting/`, `correlation_engine/`, `data_pipeline/`, `database/`, `journal_engine/`, `liquidity_engine/`, `risk_engine/`, `strategy_lab/`, `visualization/` deleted.
- **4 root-level scripts:** `main.py`, `dashboard_cli.py`, `research_cli.py`, `strategy_lab_cli.py` deleted (replaced by `traderos` entry point).
- **`infrastructure/logging.py`:** Content moved to `infrastructure/logging/__init__.py`.

### Verification
- **Lint:** 0 ruff errors
- **Typecheck:** 0 pyright errors
- **Tests:** 514 passed, coverage 75% (threshold 70%)
- **Assessment score improved:** 4.3 → 5.5 weighted

## [0.4.0] - Real-Market Wiring: Data Feed, Broker, Price Integrity
### Fixed
- **fill_price multiplier bug (Gap 3):** `PaperBrokerAdapter.place_market_order()` now returns absolute price (`close_price * slippage`) instead of just the slippage multiplier. `BrokerAdapter` ABC accepts optional `close_price` parameter. `PaperTradingService.process_candle()` no longer double-multiplies. `TradingOrchestrator.run_cycle()` passes `close_price` to broker for accurate trade records.
- **Daemon panic recovery:** `run_forever()` wraps `run_cycle()` in try/except; per-cycle errors are logged and reported to health service without crashing the daemon.

### Added
- **Real market data feed (Gap 1):** `DataIngestionService.get_latest_close(market_id)` resolves latest close price from configured collectors. Factory builds `CollectorRegistry` with `MockDataCollector` + optional `BinanceCollector`. Symbols parsed from `settings.yaml` (`data_collection.forex_symbols` + `crypto_symbols`) generate deterministic market IDs. `run_forever()` reads real prices instead of hardcoded `100.0`.
- **Alpaca broker for LIVE mode (Gap 2):** Factory branches broker selection on `TradingMode.LIVE` — uses `AlpacaBrokerAdapter` when `alpaca_api_key` + `alpaca_secret_key` are configured; falls back to `PaperBrokerAdapter` gracefully. Config typed fields `alpaca_api_key`, `alpaca_secret_key`, `alpaca_paper` with `ALPACA_API_KEY`/`ALPACA_SECRET_KEY`/`ALPACA_PAPER` env var support.
- **SPRINT_4.md** documents the sprint.

## [0.3.0] - Programme Alpha — Engineering Foundations
### Added
- **Engineering Constitution:** Ratified highest engineering authority document (docs/engineering/CONSTITUTION.md).
- **Master Execution Programme:** Operational handbook for engineering execution (docs/engineering/MASTER_EXECUTION_PROGRAMME.md).

### WP-001: Makefile & Developer Tooling Setup
- **Makefile:** Standard targets: setup, test, test-fast, test-coverage, lint, lint-fix, format, format-check, typecheck, clean, pre-commit, pre-commit-install, ci.
- **pyproject.toml:** Unified tool configuration for black, isort, ruff, pytest, coverage, pyright.
- **.pre-commit-config.yaml:** Automated hooks for trailing-whitespace, black, isort, ruff, pyright.
- **conftest.py:** Pytest session hooks for test database management.
- **Developer tooling installed:** ruff v0.16.0, black v26.5.1, isort v8.0.1, pytest v9.1.1, pytest-cov v7.1.0, pyright v1.1.411, pre-commit v4.6.1.
- **Codebase auto-formatted:** 25 files reformatted by black + isort; 107 lint issues auto-fixed by ruff.
- **.gitignore updated:** Coverage, test DBs, exports, logs patterns added.

### WP-003: Linting & Code Quality Enforcement
- **39 remaining ruff errors resolved manually:** Fixed B006, F821, BLE001, E501, G004, DTZ005, SIM103, E741, W291, UP017 violations across the codebase.
- **Resolved isort/ruff I001 conflict:** Removed I category from ruff's extend-select; isort handles import sorting per Constitution.
- **`make lint` passes cleanly:** Zero ruff errors.

### WP-004: Pyright Type Checking
- **47 pyright strict-mode errors resolved:** Fixed pandas type stub issues (NDArray→Series), read_sql_query params (tuple→list), None-unsafe returns (added asserts), sqlite3 Row type narrowing.
- **Pragmatic relaxations:** Disabled reportMissingTypeArgument, reportUnknownLambdaType, reportMissingImports for library stubs.
- **`make typecheck` passes:** Zero pyright errors.

### WP-005: Docker Containerization
- **Dockerfile:** Multi-stage Python 3.11-slim build with venv isolation.
- **docker-compose.yml:** Single-service orchestration with data/exports volumes.
- **.dockerignore:** Excludes dev artifacts, tests, docs from image.
- **DB_PATH env var support:** Database path configurable at runtime; tests use temporary paths.
- **Makefile targets:** docker-build, docker-up, docker-down added.

### WP-006: GitHub Actions CI Pipeline
- **`.github/workflows/ci.yml`:** Four-job pipeline (lint → typecheck → test → docker) with concurrency grouping, dependency caching, and coverage artifact upload.
- **Runs on push to `main`/`develop` and all PRs.**

### WP-007: Database Migration Framework
- **`database/migration_manager.py`:** Versioned schema migration engine with up/down support, automatic discovery of migration files, `_schema_version` tracking table.
- **`database/migrations/v001_initial.py`:** Initial schema migration capturing all 15 tables (market data, knowledge graph, strategy registry, etc.).
- **`database/db_manager.py` updated:** Replaced inline `_create_tables()` with `_run_migrations()` calling migration manager.
- **ADR-005:** Documented SQLite Dev / PostgreSQL Prod database strategy (`docs/adr/ADR-005.md`).

### New: Docker Compose + Entry Points + Debt Cleanup
- **`pyproject.toml`**: Added `[project.scripts]` — `traderos` (unified CLI) and `traderos-api` (FastAPI server) entry points.
- **`Dockerfile`**: Rewritten to use `pyproject.toml` → `pip install -e .[api,alpaca]`, default entry point now `traderos`.
- **`docker-compose.yml`**: Dual-service setup — `traderos` (CLI/orchestrator daemon) and `traderos-api` (FastAPI on port 8000).
- **Root scripts** (`main.py`, `strategy_lab_cli.py`): Updated to delegate to `traderos.interfaces.cli.main` (unified CLI). Backward compat maintained.
- **Unified CLI**: Import paths fixed (registry reference). All 7 command groups working.

### New: REST API + Data Ingestion Service
- **REST API** (`interfaces/api/server.py`): FastAPI server with 12 endpoints — health, strategies list/detail, backtest execute, orchestrator start/stop/status, paper session CRUD, audit trail, metrics snapshot, run manifest. Built with FastAPI + Pydantic models.
- **API entry point** (`interfaces/api/main.py`): `uvicorn.run()` on 0.0.0.0:8000.
- **`DataIngestionService`** (`domain/services/data_ingestion_service.py`): Manages data sources by market, fetches from configured collectors (MOCK/BINANCE/YFINANCE), returns normalized OHLCV dicts. Source CRUD included.
- **`pyproject.toml`**: Added `[project.optional-dependencies]` for `api` (fastapi, uvicorn), `alpaca` (alpaca-py), `all`.
- **5 tests pass.**

### New: Application Orchestrator + Broker Adapters
- **`TradingOrchestrator`** (application/orchestrator.py): Central runtime that wires all services together. Modes: PAPER, LIVE, BACKTEST. Signal-driven trading cycle: strategy evaluation → signal processing → risk assessment → trade execution. Emits events, tracks health/metrics/audit/manifest. `run_cycle()` for single-pass, `run_forever()` for daemon mode with SIGINT/SIGTERM handling.
- **`BrokerAdapter` ABC** (domain/adapters/broker_adapter.py): Polymorphic broker interface with market/limit/cancel/balance/positions.
- **`AlpacaBrokerAdapter`** (infrastructure/alpaca_broker.py): Real broker adapter using alpaca-py (optional dependency). Supports market orders, cancel, account balance, position queries. Paper/live toggle.
- **5 tests pass.**

### WP-079-091: Integration, Performance, Docs, Release
- **Integration test suite** (`tests/integration/`): 6 cross-engine tests covering strategy→backtest→risk→execution→paper pipeline, audit trail integration, metrics collection.
- **Performance benchmarks** (`tests/performance/`): 2 benchmarks — 1000-candle backtest under 1s, 1000-order execution under 100ms.
- **Sprint documentation** updated with all WP completions.
- **497 tests pass** (376 baseline + 121 new across all layers).
- **Coverage: 88.7%**, lint/typecheck pass, CI/CD ready.

### WP-071-078: Observability & Visualization
- **`MetricsService`:** Counter/gauge/timing metrics collection with named samples, snapshot export, and time-series query with limit. `TimingContext` context manager for `with`-block profiling.
- **`RunManifestService`:** Session/run recording with service, action, status, duration, metadata, and filtered retrieval.
- **`VisualizationService`:** Chart data generators for equity curves, returns distribution (bucketed), drawdown charts, and performance summary bar charts. Outputs structured `LineChart`/`BarChart` named tuples.
- **24 tests pass.**

### WP-067-070: Platform Layers (Notification, Health, Audit, CLI)
- **`NotificationService`:** Multi-channel notification system (CONSOLE/FILE/WEBHOOK) with INFO/WARNING/ERROR/CRITICAL levels, metadata support, and structured logging output.
- **`HealthService`:** Service registry with health check function execution, pass/fail reporting, history tracking, and aggregate status queries.
- **`AuditService`:** Append-only audit trail with cryptographic hash chaining, chain verification, and action/actor filtering.
- **`Unified CLI`:** Modular argparse-based CLI (`traderos.interfaces.cli.main`) with commands for strategies list/details, backtest run, paper session create/list, health status, audit trail view, and notification send.
- **26 tests pass.**

### WP-063-066: Paper Trading Engine
- **`PaperTradingService`:** Session lifecycle management (created→running→paused→stopped), signal-driven pipeline (signal→risk→portfolio→execution), equity curve tracking.
- **`PaperBrokerAdapter`:** Simulated broker with configurable slippage, fill probability, partial fills, and market/limit/stop order execution.
- **`PaperSession` entity:** Tracks session state, open/filled orders, positions, trades, equity curve, and capital allocation.
- **`DeviationAnalysisService`:** Compares paper trading vs backtest metrics (Sharpe, max DD, win rate deviations), computes correlation corridor and RMSE between return streams.
- **26 tests pass.**

### WP-059-062: Backtesting Engine
- **`BacktestingService`:** Time-series iteration over candles, strategy evaluation loop, trade simulation via `ExecutionService`, equity curve tracking, and metrics computation.
- **Metrics:** Sharpe/Sortino/Calmar ratios, max drawdown, win rate, profit factor, recovery factor — all using sample standard deviation (ddof=1).
- **5 tests pass.**
- **`BacktestStep` NamedTuple** captures per-bar equity, order, and fill price for granular analysis.

### WP-008: Namespace Package Restructuring
- **New layered structure under `src/traderos/`:**
  - `domain/` — `analysis/`, `liquidity/`, `risk/`, `strategies/`, `backtesting/`, `research/`
  - `infrastructure/` — `config/`, `database/`, `data/`
  - `application/` — `orchestrator.py`
  - `interfaces/` — `cli/`, `visualization/`
- **Dual directory strategy:** Old flat modules (`analysis_engine/`, `database/`, etc.) become re-export shims preserving backward compatibility.
- **All internal imports updated** to `traderos.domain.*`, `traderos.infrastructure.*`, `traderos.application.*`, `traderos.interfaces.*`.
- **Tooling configs updated:** pyproject.toml (pyright extraPaths, coverage source), Makefile (PYTHONPATH=src), Dockerfile (ENV PYTHONPATH), CI workflow.
- **Entry point scripts** (`main.py`, `dashboard_cli.py`, `research_cli.py`, `strategy_lab_cli.py`) become thin wrappers that add `src` to path and delegate to new structure.

### AI Engineering Operating System
- **`.ai/context/` — 13 permanent context files** enabling any AI model to understand the project instantly:
  - Architecture, system map, domain model, code standards, DB contracts, ADR decisions, release readiness, security (core + subsystems), roadmap, workflow rules, UI context, playbook, and meta-files (cross-reference, dependency graph, maintenance, expansion).
- **`.ai/agents/` — 9 AI agent files** defining mission, inputs, outputs, and interaction protocols for planner, builder, auditor, reviewer, migration, performance, security, product, and release agents.
- **Layer 3 meta-files:** Cross-reference matrix, dependency graph, maintenance guide, future expansion strategy.
- **Design:** Files follow strict template, use references (never copies), and are versioned alongside TraderOS. Maintained via the `.ai/VERSION` convention and `99_maintenance_guide.md`.

## [0.2.0] - 2026-06-01
### Added
- **Strategy Lab:** New module for developing and registering trading strategies.
- **Starter Strategies:** Moving Average Trend, Volatility Breakout, and Mean Reversion.
- **Backtest Engine:** Historical replay system with commissions, spread assumptions, and equity curve generation.
- **Risk Engine:** Volatility-based position sizing, exposure limits, and kill switch framework.
- **Knowledge Graph Integration:** Backtest results can now be linked directly to research hypotheses.
- **Strategy Lab CLI:** Command-line interface for running backtests and managing strategies.

### Fixed
- Timezone mismatch in correlation engine.
- Session statistics database schema synchronization.

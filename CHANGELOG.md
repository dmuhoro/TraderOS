# Changelog - TraderOS

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

# 02 — System Map

## Purpose
Complete repository map. Every file, every package, every relationship. Used by AI agents to navigate the codebase without searching.

## Authority Level
**Foundational** — ground truth for codebase structure.

## Consumers
All AI agents, CI/CD pipelines, onboarding.

## Dependencies
- `.ai/context/01_architecture.md` — for layer classification

## Source Documents
- Repository file tree
- `src/traderos/` package structure

## Update Rules
- Update when files are added, moved, or removed
- Update when responsibilities change
- Keep synced with WP-008 package structure

---

## Repository Root

```
/ (TraderOS)
├── .ai/                    # AI Engineering Operating System
│   ├── context/            # Permanent context for AI agents
│   └── agents/             # Specialised agent operating manuals
├── .github/workflows/      # CI/CD pipelines
├── configs/                # Backward-compat config shim → src/
├── database/               # Backward-compat database shim → src/
├── docs/
│   ├── adr/                # Architecture Decision Records
│   └── engineering/        # Constitution, Master Execution Programme
├── src/traderos/           # PRIMARY PACKAGE
│   ├── domain/
│   │   ├── analysis/       # indicators.py, correlation.py
│   │   ├── backtesting/    # engine.py
│   │   ├── liquidity/      # swing, zone, sweep, breakout, session
│   │   ├── research/       # logger.py, research_engine.py
│   │   ├── risk/           # engine.py
│   │   └── strategies/     # base_strategy.py, strategies.py
│   ├── infrastructure/
│   │   ├── config/         # config_loader.py
│   │   ├── data/           # collectors.py, pipeline.py
│   │   └── database/       # db_manager.py, migration_manager.py, migrations/
│   ├── application/        # orchestrator.py
│   └── interfaces/
│       ├── cli/            # dashboard.py, research.py, strategy_lab.py
│       └── visualization/  # charts.py, liquidity_charts.py
├── tests/                  # Test suite
├── main.py                 # Entry point (thin wrapper)
├── dashboard_cli.py        # Entry point (thin wrapper)
├── research_cli.py         # Entry point (thin wrapper)
├── strategy_lab_cli.py     # Entry point (thin wrapper)
├── Makefile                # Build automation
├── pyproject.toml          # Tool configuration
├── Dockerfile              # Container definition
├── docker-compose.yml      # Service orchestration
└── conftest.py             # Pytest session config
```

## Package Dependency Graph

```
traderos.application.orchestrator
    ↓                    ↓              ↓
traderos.domain.*    traderos.infrastructure.*    traderos.interfaces.*
    ↓                    ↓
    └──── traderos.infrastructure.database ────┘
    └──── traderos.infrastructure.config ────┘
    └──── traderos.infrastructure.data ────┘

Internal domain dependencies:
    domain.analysis  → domain.liquiquity (none)
    domain.strategies → domain.analysis
    domain.backtesting → domain.strategies, domain.risk
    domain.research → domain.backtesting (through events)
```

## Module Responsibility Matrix

| Module | Files | Lines | Test Coverage | Responsibility |
|--------|-------|-------|---------------|----------------|
| `domain/analysis` | 2 | 84 | 76% | Indicators, regime detection, correlations |
| `domain/backtesting` | 1 | 65 | 95% | Historical replay, equity curves, metrics |
| `domain/liquidity` | 5 | 111 | 0% | Swings, zones, sweeps, breakouts, sessions |
| `domain/research` | 2 | 60 | 96% | Observations, hypotheses, experiments, lessons |
| `domain/risk` | 1 | 22 | 91% | Position sizing, kill switch, exposure |
| `domain/strategies` | 2 | 85 | 85% | Strategy registry, built-in strategies |
| `infrastructure/config` | 1 | 23 | 65% | YAML + .env loading |
| `infrastructure/data` | 2 | 111 | 0% | Collectors (CCXT, YFinance), pipeline |
| `infrastructure/database` | 2+ | 97 | 86% | DB manager, migrations, schema |
| `application/orchestrator` | 1 | 86 | 0% | Pipeline orchestration |
| `interfaces/cli` | 3 | 132 | 0% | CLI entry points |
| `interfaces/visualization` | 2 | 51 | 0% | Charts, heatmaps, liquidity maps |

## Key Files

| File | Purpose |
|------|---------|
| `src/traderos/infrastructure/database/db_manager.py` | All database operations |
| `src/traderos/infrastructure/database/migration_manager.py` | Schema versioning |
| `src/traderos/application/orchestrator.py` | Main execution pipeline |
| `src/traderos/domain/analysis/indicators.py` | Technical analysis |
| `src/traderos/domain/strategies/strategies.py` | Strategy registry |

## References
- Master Execution Programme §12 — Work Breakdown Structure
- Constitution §6 — Domain Model
- `.ai/context/01_architecture.md` — layer boundaries

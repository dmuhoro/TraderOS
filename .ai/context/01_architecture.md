# 01 — System Architecture

## Purpose
Authoritative reference for TraderOS system architecture — layers, boundaries, dependency rules, and extension points. Every AI agent and engineer must consult this before making structural changes.

## Authority Level
**Foundational** — supplements Constitution [C:4, C:5]. Overrides ad-hoc architectural decisions.

## Consumers
All AI agents, engineers, code reviewers, architects.

## Dependencies
- `docs/engineering/CONSTITUTION.md` [C:4 Engineering Vision, C:5 Target Architecture]

## Source Documents
- Constitution Sections 4-5
- Master Execution Programme Sections 3, 7

## Update Rules
- Update when new layers or modules are added
- Update when dependency rules change
- Must be approved by monthly architecture review

---

## System Vision

TraderOS is a **modular event-driven monolith** with clearly bounded contexts, communicating through a lightweight event bus, persisted in a relational database, and observable through structured logging and metrics.

## Layered Architecture

```
┌──────────────────────────────────────────────┐
│              INTERFACE LAYER                  │
│  CLI  │  REST API  │  Dashboard  │  WebSocket │
└──────────────────────┬───────────────────────┘
                       │
┌──────────────────────▼───────────────────────┐
│             APPLICATION LAYER                 │
│  Orchestrator  │  Scheduler  │  Workers       │
│  Event Bus     │  Pipeline   │  Jobs          │
└──────────────────────┬───────────────────────┘
                       │
┌──────────────────────▼───────────────────────┐
│               DOMAIN LAYER                    │
│  Market Data  │  Analysis  │  Liquidity       │
│  Signals      │  Risk      │  Portfolio       │
│  Execution    │  Research  │  Knowledge Graph │
└──────────────────────┬───────────────────────┘
                       │
┌──────────────────────▼───────────────────────┐
│           INFRASTRUCTURE LAYER                │
│  Database  │  Config  │  Data Pipeline       │
│  Cache     │  Logging │  Metrics             │
└──────────────────────────────────────────────┘
```

## Module Boundaries

| Module | Layer | Responsibility |
|--------|-------|---------------|
| `domain/market_data` | Domain | Market normalization, validation |
| `domain/analysis` | Domain | Indicators, regimes, correlations |
| `domain/liquidity` | Domain | Swings, zones, sweeps, breakouts, sessions |
| `domain/risk` | Domain | Position sizing, exposure, kill switch |
| `domain/strategies` | Domain | Strategy registry, evaluation |
| `domain/backtesting` | Domain | Historical replay, metrics |
| `domain/research` | Domain | Observations, hypotheses, experiments, lessons |
| `domain/execution` | Domain | Order lifecycle, broker abstraction |
| `domain/portfolio` | Domain | Portfolio state, allocation, performance |
| `infrastructure/database` | Infrastructure | SQLite/Postgres, repositories, migrations |
| `infrastructure/config` | Infrastructure | Settings, env vars, secrets |
| `infrastructure/data` | Infrastructure | Collectors, pipeline, normalizers |
| `application/*` | Application | Orchestration, scheduling, event bus |
| `interfaces/cli` | Interface | Command-line entry points |
| `interfaces/visualization` | Interface | Charts, dashboards |

## Dependency Rules

1. **Dependencies point inward**: Interface → Application → Domain ← Infrastructure
2. **Domain imports NOTHING from Infrastructure or Application**
3. **Infrastructure imports from Domain (interfaces) and config**
4. **Application imports from Domain only**
5. **Interface imports from Application only**
6. **No circular dependencies between domain modules**
7. **Research depends on NO domain module** — it consumes events
8. **Current state** (Phase 1): Domain still imports infrastructure directly. This is a known technical debt tracked as WP-009 through WP-014.

## Execution Pipeline

```
Data Collection → Normalization → Feature Computation
    ↓                                                  │
Regime Detection → Liquidity Analysis → Signal Gen    │
    ↓                          ↓                      │
Risk Validation → Portfolio Check → Execution         │
    ↓                                                  │
Research Logging → Knowledge Graph Update ─────────────┘
```

## Package Ownership

| Package | Owner | Review Required |
|---------|-------|----------------|
| `traderos/domain/*` | Domain Team | Architecture review |
| `traderos/infrastructure/*` | Infrastructure Team | Security review |
| `traderos/application/*` | Platform Team | Architecture review |
| `traderos/interfaces/*` | Frontend Team | UX review |

## Extension Points

- **Strategy Registry**: Add new strategies in `domain/strategies/`
- **Data Collectors**: Add new sources in `infrastructure/data/collectors.py`
- **Broker Adapters**: Add new brokers via `domain/execution/broker_adapter.py`
- **Indicators**: Add new indicators in `domain/analysis/indicators.py`
- **Migrations**: Add new versions in `database/migrations/`

## Anti-patterns

| Anti-pattern | Why | Instead |
|-------------|-----|---------|
| Importing `sqlite3` in domain code | Violates dependency rule | Use repository interface |
| Circular domain imports | Creates tight coupling | Extract shared types to `domain/types` |
| Business logic in CLI scripts | Untestable | Thin wrappers delegating to domain |
| Configuration in code | Not deployable | Use `infrastructure/config` |
| Direct DB access from interfaces | Bypasses domain rules | Go through application layer |
| God modules (>500 lines) | Hidden coupling | Split by concept |

## References
- [C:4] Engineering Vision — architecture rationale
- [C:5] Target System Architecture — full system diagrams
- Master Execution Programme §3 — transition strategy
- Master Execution Programme §7 — epics
- WP-008 — namespace package restructure
- WP-009 — domain entity dataclasses
- WP-010 — repository interfaces

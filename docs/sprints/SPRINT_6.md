# Sprint 6 — v1 Readiness: Architecture Hardening & Production Safety

**Period:** 27 July 2026
**Branch:** `sprint-2-paper-trading`
**Status:** IN PROGRESS
**Version Target:** v0.8.0

---

## Goal

Close the remaining architectural gaps between TraderOS and a production-grade v1 release. Work is organised in 10 sequential layers, each building on the previous. Every layer produces a clean commit with passing lint/typecheck/tests.

---

## Layer Plan

| Layer | Focus | Key Files |
|-------|-------|-----------|
| 1 | **Ports & Dependency Inversion** — Extract protocols for Broker, EventBus, Notification, Audit, Metrics, Health, Manifest, Repositories. Make domain depend on protocols only. | `domain/ports.py`, `domain/adapters/`, `infrastructure/*`, `application/factory.py` | ✅ |
| 2 | **Eliminate Global Mutable State** — Replace Config singleton, module-level StrategyRegistry, module-level `_orchestrator` with dependency injection. | `config_loader.py`, `strategy_framework.py`, `server.py` |
| 3 | **Kill Switch & Circuit Breaker** — Implement hard trade-rejection limits in RiskService, wire into orchestrator loop. Add daily-loss, max-position, concentration limits. | `domain/services/risk_service.py`, `application/orchestrator.py` |
| 4 | **API Authentication** — API-key middleware on all endpoints. Key from env var. Integration tests. | `interfaces/api/server.py` |
| 5 | **Secrets Management** — Move all secrets to env vars only, startup validation, no plaintext in config files. | `infrastructure/config/config_loader.py`, `factory.py` |
| 6 | **Observability Persistence** — Back audit, metrics, health, manifest to SQLite. Database migrations for new tables. | `infrastructure/audit.py`, `infrastructure/metrics.py`, `infrastructure/health.py`, `infrastructure/run_manifest.py` |
| 7 | **Order Management** — Order lifecycle state machine, order persistence, position management (close_position, realized PnL). Fix paper limit orders. | `domain/entities/trade.py`, `domain/entities/position.py`, `domain/services/portfolio_service.py` |
| 8 | **Orchestrator Decomposition** — Extract PipelineRunner (cycle logic), DaemonController (lifecycle), CycleExecutor (per-market cycle). | `application/orchestrator.py`, `application/cycle_executor.py`, `application/daemon_controller.py` |
| 9 | **Testing** — Alpaca broker mock tests, API integration tests, CLI tests, push coverage to ≥85%. | `tests/`, `infrastructure/alpaca_broker.py` |
| 10 | **Production Hardening** — Rate limiting/backoff, strategy registry persistence, config validation improvements, data archival. | Multiple files |

---

## Assessment Score Target

| Category | Before (v0.7.0) | Target (v1.0) |
|----------|:----------------:|:--------------:|
| Infrastructure | 6 | 8 |
| Trading System | 5 | 8 |
| Configuration | 7 | 9 |
| Architecture | 5 | 8 |
| **Weighted Total** | **~6.2** | **~8.5** |

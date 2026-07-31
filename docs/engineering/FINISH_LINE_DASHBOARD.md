# TraderOS — Finish Line Dashboard

**Version:** 1.0
**Date:** 2026-08-01
**Programme:** C — Commercial Surface (C5)
**Basis:** Authoritative description of the operator-facing commercial surface: the enforced operator workflow (C2), the operator REST API (C1), the strategy catalog (C3), and the session report (C4). Every claim below is pinned to the implementation.

> **Purpose.** This is the canonical design document for the Finish Line Dashboard — the screen a human operator sees when they "sign in and run the platform." It describes the enforced session lifecycle, every dashboard endpoint, the strategy catalog lifecycle, and the session report contract, and it is the reference for the Programme C delivery claim.

---

## 1. What the Dashboard Is

The Finish Line Dashboard is the read/write surface that lets a single operator take a TraderOS instance from *idle* to *a completed, documented session*:

```
idle ──▶ start ──▶ preflight ──▶ broker_check ──▶ market_data_check
        ──▶ paper_trading ──▶ performance_review ──▶ strategy_promotion
        ──▶ controlled_live ──▶ shutdown ──▶ session_report
```

The dashboard is **not** a free-form trading terminal. It drives a strictly ordered workflow; a step cannot be skipped or reordered. This is the commercial differentiator: a human being is in control, the system refuses out-of-order actions, and every action is recorded in an audit-style transition log.

---

## 2. The Enforced Workflow (C2)

**Source:** `src/traderos/domain/services/operator_workflow.py`, `src/traderos/domain/services/operator_session.py`

- `OperatorStep` — 10 canonical steps (start, preflight, broker_check, market_data_check, paper_trading, performance_review, strategy_promotion, controlled_live, shutdown, session_report).
- `OperatorWorkflow` — pure state machine. `can_advance_to()` allows only the immediate next step, or a re-run of the current step (except `start` and `session_report`). `advance()` raises `WorkflowError` otherwise.
- `OperatorSessionService` — the gated executor. Each step runs a real check via `_gate_*` handlers:
  - `preflight` → `PreflightService.check(live_mode=...)`; a failing preflight does **not** advance.
  - `broker_check` → broker balance + broker-state reconciliation.
  - `market_data_check` → `DataIngestionService` feed count.
  - `paper_trading` → running `PaperTradingService` sessions.
  - `performance_review` → `StrategyCatalogService.compare()` ranking of enabled strategies.
  - `strategy_promotion` → `StrategyCatalogService.promote(name)` (requires a name in the request).
  - `controlled_live` → live-mode preflight; requires a passing live preflight.
  - `shutdown` → stops all running paper sessions.
  - `session_report` → terminal step; marks the workflow completed.
- Every successful transition is persisted through `OperatorWorkflowRepository` (SQLite `workflow_transitions`/`workflow_state` tables, migration `v006_operator_surface`).

### 2.1 Step semantics contract

| Step | Gate | Advances only if | Failure behavior |
|---|---|---|---|
| start | session id binding | always | n/a (not re-runnable) |
| preflight | preflight verdict | passed | re-run allowed |
| broker_check | balance + reconciliation | connected and reconciled | re-run allowed |
| market_data_check | feed count > 0 | at least one source | re-run allowed |
| paper_trading | paper engine ready | sessions present | re-run allowed |
| performance_review | catalog ranking | ≥ 1 enabled strategy | re-run allowed |
| strategy_promotion | promote() success | name provided and valid | re-run allowed |
| controlled_live | live preflight | passed | re-run allowed |
| shutdown | paper stop | always | re-run allowed |
| session_report | report generation | always | terminal |

---

## 3. The Operator REST API (C1)

**Source:** `src/traderos/interfaces/api/operator.py`, registered from `src/traderos/interfaces/api/server.py` under the `/v1` prefix.

### 3.1 Read endpoints (the dashboard panels)

| Endpoint | Response | Panel |
|---|---|---|
| `GET /v1/positions` | open positions with market, qty, entry/current price, pnl | Positions |
| `GET /v1/orders` | open orders from the broker adapter | Orders |
| `GET /v1/trades` | trade log (sorted by created_at, `limit` param) | Trades |
| `GET /v1/portfolio` | total equity, cash, positions value, total pnl, count | Portfolio |
| `GET /v1/equity-curve` | equity points per position update + current | Equity curve |
| `GET /v1/pnl` | realized / unrealized / total pnl | P&L |
| `GET /v1/kill-switch` | engaged, reason, circuit open, consecutive failures | Kill switch |
| `GET /v1/preflight` | passed + per-check detail | Preflight |
| `GET /v1/readiness` | ready flag + preflight/data-feed/broker checks | Health |
| `GET /v1/workflow` | current step, next step, status, session id, history | Session |
| `GET /v1/strategies` | catalog strategies (name, template, params, status, version) | Catalog |
| `GET /v1/strategies/{name}` | single strategy detail | Catalog |
| `GET /v1/strategies/{name}/review` | backtest review of a strategy | Catalog |
| `GET /v1/reports/session` | full session report (JSON) | Report |
| `GET /v1/reports/session?fmt=markdown` | session report as Markdown | Report |

### 3.2 Write endpoints

| Endpoint | Action |
|---|---|
| `POST /v1/kill-switch/engage` | open the circuit breaker immediately (blocks all trading) |
| `POST /v1/kill-switch/disengage` | clear the circuit + failure counters |
| `POST /v1/workflow/advance` | attempt one workflow step; body `{step, actor, strategy?, session_id?}` |
| `POST /v1/strategies` | create a catalog strategy from a template (`{name, template, params?}`) |
| `POST /v1/strategies/{name}/enable` \| `/disable` \| `/promote` \| `/archive` \| `/clone` | strategy lifecycle |
| `POST /v1/strategies/compare` | compare strategies and return a ranking |

### 3.3 Error semantics

| Code | Meaning |
|---|---|
| 400 | unknown workflow step; `StrategyLifecycleError` (bad template, duplicate, illegal lifecycle move) |
| 404 | unknown strategy |
| 409 | out-of-order workflow transition (`WorkflowError`) |
| 501 | capability not configured (no catalog / no workflow / no preflight) |

---

## 4. The Strategy Catalog (C3)

**Source:** `src/traderos/domain/services/strategy_management.py`

- Three built-in templates are seeded on first boot: `moving_average_trend`, `volatility_breakout`, `mean_reversion` (state `active`).
- Lifecycle states: `draft → active → disabled / archived`, plus exactly one `promoted` at a time (promoting a second demotes the first).
- Strategies are versioned; `clone` creates a new draft copy.
- The execution loop consumes **enabled** strategies only, via the `enabled_strategies` callable bound in the orchestrator (`src/traderos/application/orchestrator.py`).
- `compare(names)` backtests each named strategy and returns a ranking + metrics — this feeds the `performance_review` step.

---

## 5. The Session Report (C4)

**Source:** `src/traderos/domain/services/session_report.py`

The report is an immutable snapshot of one operator session:

| Field | Content |
|---|---|
| `session_id`, `generated_at` | identity + timestamp |
| `workflow_status`, `current_step` | session lifecycle position |
| `steps` | the full transition log (from → to → actor → result → timestamp) |
| `portfolio` | equity, cash, positions value, total pnl, count |
| `positions`, `trades` | detailed position and trade lists |
| `strategies`, `promoted_strategy` | catalog state incl. the currently promoted strategy |
| `risk` | kill-switch state (engaged, reason, circuit, consecutive failures) |
| `duration_seconds` | start→complete elapsed time |

Exports: JSON (default) and Markdown via `to_json()` / `to_markdown()`; both served by the `/reports/session` endpoint.

---

## 6. Definition of Done for C5

- [x] Enforced workflow can be driven end-to-end by the API (start → … → session_report) and rejects out-of-order steps with 409.
- [x] All read panels return 200 on a live orchestrator build.
- [x] Kill switch engage/disengage reflected in `/v1/kill-switch` and respected by trading.
- [x] Strategy lifecycle (create/enable/disable/promote/archive/clone/compare/review) exposed and rule-enforced.
- [x] Session report endpoint produces JSON and Markdown snapshots of a session.
- [x] Full gate: ruff + pyright clean, pytest coverage ≥ 70% (currently 85%+), all new API/report tests green.

**Evidence:** `tests/test_operator_api.py`, `tests/test_session_report.py`, `tests/test_operator_session.py`, `tests/test_strategy_management.py`, `tests/test_operator_workflow.py`, `tests/test_cycle_gate.py`.

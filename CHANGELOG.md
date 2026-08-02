# Changelog - TraderOS

## [Unreleased] - Sprint 22 (Postgres reproducibility — environment-independent CI signal)

### Postgres reproducibility programme (2026-08-02)
- **Root cause fixed (test-harness only, no `src/` changes):** an independent
  cold-environment audit against `d52f0bd` found 51 test errors, 100% from one
  cause (Postgres unreachable at `localhost:5433`, no skip guard) and zero
  application-logic defects. A short-timeout reachability probe now guards the
  Postgres-backed modules so they **skip** (honest reason, visibly not a pass)
  when no Postgres is reachable — and **run for real** when one is.
- **Guarded:** `tests/test_postgres_repositories.py`,
  `tests/test_observability_postgres.py`,
  `tests/test_observability_postgres_services.py`, and
  `TestV004Postgres` in `tests/test_migration_v004.py`
  (its sqlite tests still run without Postgres).
- **CI:** verified (not assumed) `ci.yml`'s `test` job provisions the Postgres
  service and documented it, so CI exercises the pass path, not the skip path.
- **Evidence (both environments, 0 failures/0 errors, only skips differ):**
  - WITH Postgres → `1274 passed, 1 skipped` (`docs/evidence/2026-08-02_postgres_with_pg.log`);
  - WITHOUT Postgres → `1219 passed, 56 skipped` (`docs/evidence/2026-08-02_postgres_without_pg.log`).
- **Governance:** merged `docs/engineering/AUDIT_GROUND_TRUTH.md` verbatim into
  canonical `docs/AUDIT_GROUND_TRUTH.md` (§7 delta + appendix), deleted the
  redundant copy, repointed internal links; `NEXT_STEPS_TO_COMPLETION.md`
  marks WP-N1 DONE, folds WP-N0, closes WP-N2.

## [Unreleased] - Sprint 21 (Order-Survivability: durable journal wire-up L1-L4)

### Order-Survivability Sprint (2026-08-02)
- **L1 — durable, idempotent order path**: new `infrastructure/journaled_broker.py`
  `JournaledBroker` persists intent before the broker (`CONFIRMED` on success),
  dedupes by a derived `uuid5` key, and replays the stored result on restart
  (no duplicate submit). Wired into LIVE mode via `factory.build_orchestrator`
  (best-effort). `journal.py` gained `get/update/count`.
- **L2 — restart drill**: `docs/evidence/2026-08-02_l2_restart_surprise_rehearsal.log`
  shows broker submit `0` on replay, intent drift blocking `can_accept_orders`,
  `unconfirmed_intent` mismatch surfaced. `MismatchType.UNCONFIRMED_INTENT` +
  `journal_pending` added to reconciliation.
- **L3 — runbook→CLI parity** (CLOSURE-14): `risk` (status/check/reset/kill/reconcile),
  `metrics` (snapshot/watch), `daemon start` alias, `audit verify`. All hands-on PASS.
- **L4 — last live-dependency drills (real network/Postgres, not fabricated)**:
  - **R-01 Binance live**: REST klines + live WS `@kline_1m` through the OT-004
    pipeline → PASS (`docs/evidence/2026-08-02_l4r01_binance_live.log`).
  - **R-02 Postgres crash**: `traderos-pg-test` crashed → boundary failed closed
    (`connection-refused`) → restarted healthy → marker row survived → PASS
    (`..._l4r02_postgres_crash_drill.log`).
- **Gate**: full suite **1274 passed, 1 skipped**, coverage **92.83%**; ruff 0;
  black/isort unchanged; pyright strict clean.
- **Honest residual**: L5 (real-money pilot + switch) intentionally gated on
  explicit operator funding/approval — not fabricated.

## [Unreleased] - Sprint 20 (Programme Ω — First genuine execution evidence)

### Programme Ω (2026-08-02)
- **Bootstrap fix**: `Config.load()` now auto-creates runtime dirs (`data_dir`, `exports_dir`, `db_path` dir), so `pilot dry-run` works from a genuinely fresh checkout (`test_load_creates_missing_db_directory` regression test).
- **First real execution evidence** (all logged under `docs/evidence/2026-08-02_*.log`, secrets redacted):
  - **Alpaca paper dry-run rehearsal** against a **real paper account**: connected with `alpaca-py 0.43.5`, reconciled broker state (`can_accept_orders=True`, real balance 100,000), operator workflow `READY` with live execution disabled (`dry_run=True`), exit **0**.
  - **Backup → restore drill**: SHA-256 round-trip equal (`b91b07a…`), marker row preserved, `PRAGMA integrity_check` ok.
  - **Migration rollback drill**: schema 6 → 3 → 6 with integrity `ok` at each step.
- **Real defects surfaced & fixed by the genuine run**:
  - `AlpacaBrokerAdapter.get_open_orders()` used an incompatible `get_orders(status="open")` call → now `GetOrdersRequest(QueryOrderStatus.OPEN)` (alpaca-py 0.43.5 API); test mock + assertion updated.
  - `factory.py` built `PaperTradingService` only in `PAPER` mode, so the LIVE-mode operator workflow hard-failed at the paper gate; now built for `LIVE` too (harmless under `dry_run=True`), letting the rehearsal complete.
- **Governance**, evidence-only: `NEXT_STEPS_TO_COMPLETION.md` Ω trackers → DONE; `FINISH_LINE_DASHBOARD.md` Deployment Readiness 72→74 + PRI note; `AUDIT_GROUND_TRUTH.md` §10 delta.
- **Gate**: full suite **1266 passed, 1 skipped**; `ruff check .` 0 errors; black/isort/pyright strict clean.
- **Honest residual (still open, not fabricated)**: real-money live pilot, Binance live (R-01), Postgres failure drill (R-02), durable journal wire-up (CLOSURE-12), runbook→CLI parity (CLOSURE-14).

## [Unreleased] - Sprint 19 (Engineering Closure & Code Freeze Preparation)

### Engineering Closure pass (2026-08-02)
- **Build green**: installed missing `prometheus-client` (pinned) so `/metrics` returns **200** (was 501); fixed the previously failing `test_health_and_metrics_stay_open`.
- **Lint green**: fixed **22 ruff errors** — 5 in `src` (E501) + 17 in `tests` (SIM102/SIM117/RUF059/F841/PLW1510/BLE001). Introduced a `_CYCLE_EXCEPTIONS` alias in `cycle_executor.py`/`daemon_controller.py` to deduplicate the repeated exception tuple. Black/isort reformat of 6 flagged files.
- **Full suite**: **1266 passed**, coverage **93.62%**; `make ci` green (ruff, black, isort, pyright strict, pytest).
- **Security measured**: `pip-audit` 0 known vulnerabilities; `bandit -r src/traderos -lll` 0 High (Medium = known B608 f-string-SQL false positives).
- **Dead code removed**: deleted dead stubs `DaemonController._is_market_hours` (always `True`) and `DaemonController._drain_open_orders` (fake audit event).
- **Release docs**: replaced aspirational placeholders with verified `ENGINEERING_CLOSURE_AUDIT.md`, honest `FINISH_LINE_DASHBOARD.md`, `ENGINEERING_CLOSURE_REPORT.md`; delta sections added to `AUDIT_GROUND_TRUTH.md` and `STRATEGIC_COMPLETION_BLUEPRINT.md`.
- **Closure backlog opened (no speculative features)**: live-connectivity drills, replay wiring (CLOSURE-12), runbook→CLI parity, controlled pilot.

## [Unreleased] - Sprint 18 (Coverage to 91.8% + Production Security Hardening)

### WP-1 — Close the coverage gap (86.80% → 91.82%)
- **Layer 1a — flagged modules to unit coverage**: `market_hours_engine` 38% → 98%, `webhook_notifier` 43% → 84%, `leader_election` 58% → 97%, `message_queue` 67% → 100%, `interfaces/api/main.py` 33% → 94%.
- **Layer 1b — PostgreSQL-backed coverage** against the `traderos-pg-test` container (port 5433, `POSTGRES_TEST_DSN`): `observability_postgres` 35% → 99%, `postgres/base` 39% → 94%, `postgres/signals` 51% → 100%, `postgres/trades` 41% → 100%.
- **Layer 1c — mop-up**: `sqlite/knowledge` 55% → 100% (incl. `get_neighbors` BFS), `in_memory/indicators` 67% → 100%, `v004` migration 69% → 100%, `migration_utils` 27% → 100%.
- **Latent bugs fixed by the new tests**:
  - `webhook_notifier.py` — `retry_with_backoff` raises `ServiceError`, which was never caught; webhook failures now surface as logged warnings instead of leaking.
  - `market_hours_engine.py` — 24h sessions mis-handled when `open == close`; `FOREX_24_5`/`CRYPTO_24_7` conflated by structural `==` on the frozen dataclass (now identity checks); `next_open` never advanced past "after close" / weekends.
- New test files: `test_market_hours_engine.py`, `test_webhook_notifier.py`, `test_observability_postgres_services.py`, `test_postgres_repositories.py`, `test_migration_v004.py`.

### WP-2 — Production security hardening (fail-closed posture)
- **`infrastructure/security_policy.py`** (new): `TRADEROS_ENV=production` now requires API keys and TLS and forbids CORS allow-all; development/CI stay open-by-default and frictionless. `SecurityPolicyError` is raised on violation; `check_security_posture()` produces a machine-readable `SecurityReport`.
- **API entrypoint fails closed**: `interfaces/api/main.py` refuses to start the server in production until keys + TLS are configured.
- **`traderos security audit`** CLI: reports auth/TLS/CORS/secret-rotation posture per environment, exits non-zero when insufficient (evidence for the pilot gate).

### Verification
- **1201 tests passing, 1 skipped** (full suite), **91.82% coverage** (threshold 70%), **ruff 0 errors** and **pyright 0 errors** on all changed files. Sprint report: `docs/sprints/SPRINT_18.md`.

## [Unreleased] - Sprint 17 (Pilot Readiness — Order Surface, Service Wiring, Security Hardening, Pilot CLI)

### WP-2 — Order surface
- **Broker ABC** (`domain/adapters/broker_adapter.py`): `place_stop_order`, `place_trailing_stop_order`, `modify_order`.
- **Alpaca** (`infrastructure/alpaca_broker.py`): all three implemented via `replace_order_by_id` (qty → int, prices rounded).
- **Paper broker** (`domain/services/paper_trading_service.py`): stateful adapter (`_positions`, `_open_orders`, `_order_seq`, `_apply_fill`, `_record_order`); stop/trailing stops become real guarded limit orders (trigger only when `market_price is not None`).
- **`OrderStatus.MODIFIED`** (`domain/services/execution_service.py`); **rate-limiter** pass-throughs for the three new methods.

### WP-3 — Service wiring
- **BACKTEST mode** now runs enabled strategies through `BacktestingService.run` on fetched/synthetic candles; per-strategy `run_manifest` + `backtest.complete` events; missing service records a `ServiceError` instead of crashing the cycle.
- **Regime + breakout analysis** run each cycle and publish `cycle.analysis` events (`payload: {market_id, regime, breakout_events}`).
- **Trade evidence**: post-fill hook creates knowledge-graph market/strategy nodes with `trades_in`/`has_strategy` edges and a `research.create_observation` entry.
- **Factory** wires in-memory `KnowledgeGraphService` + `ResearchService` (all five research repos) and wraps the broker as `GuardrailedBroker(RateLimitedBroker(broker))`.

### WP-4 — Security hardening
- **`GuardrailedBroker`** (`infrastructure/order_guardrail.py`, enabled by default): rejects `qty < TRADEROS_MIN_ORDER_QTY` (default 1.0) or notional > `TRADEROS_MAX_ORDER_NOTIONAL` (default 500.0); rejections return `FillResult(..., "rejected", reason)` so they count against the kill-switch failure counter. Covers market/limit/stop/trailing/modify-qty.
- **CORS**: `CORS_ORIGINS` now defaults to `""` (deny-all browser CORS); explicit `*` or comma-separated origins to enable.
- `docs/runbooks/CONTROLLED_PILOT.md` gains an **Order-Size Guardrails** section with pilot values.

### WP-5 — Pilot readiness
- **`traderos pilot readiness`** — runs the live-readiness gate (human table or `--json`), exits 0 only when ready; **`traderos pilot dry-run`** rehearses the operator workflow end to end with `dry_run=True`, driving the state machine from its current step, skipping strategy promotion (operator decision), stopping at the first failing gate.
- **`docs/runbooks/PILOT_READINESS.md`** — readiness checks, dry-run flow, six go/no-go gates, controlled-live procedure, exit criteria.
- CLI tests for both pilot subcommands.

### Verification
- **1060 tests passing, 1 skipped** (full suite), **86.80% coverage** (threshold 70%), **ruff 0 errors** and **pyright 0 errors** on all changed files. Sprint report: `docs/sprints/SPRINT_17.md`.

## [Unreleased] - Sprint 16 (Programme C — Auth, Observability, Dashboard, Live Verification, Ops)

### WP-3 — Auth / RBAC
- **API-key authentication** (`infrastructure/auth.py` + `interfaces/api/security.py`): `TRADEROS_ADMIN_API_KEY`/`TRADEROS_OPERATOR_API_KEY`/`TRADEROS_VIEWER_API_KEY` (legacy `TRADEROS_API_KEY` → admin). Open-by-default: enforcement activates only when keys are configured.
- **Role-scoped dependencies** `require_read`/`require_operate`/`require_admin` applied to every protected route; `GET /v1/auth/me` returns the authenticated principal. Health, `/metrics`, and the `/dashboard` static mount stay open.

### WP-4 — Observability
- **`EventBroker`** (`interfaces/api/events.py`): thread-safe bounded buffer (maxlen 50, drop-oldest) with blocking get; `get_broker`/`reset_broker`/`publish_event`.
- **`/v1/events` SSE** endpoint: snapshot-first, 15 s keepalives, clean unsubscribe; testable `operator.event_stream(...)` async generator factored out of the route. Fixed a stream-blocking bug (`to_thread(sub.get, timeout=...)` → `sub.get(True, 15)`).
- **Kill-switch alerting** via `NotificationLevel.CRITICAL`/`WARNING` with `metadata={"source": "operator_api"}`.
- **Binance gating**: real crypto feed only when `data_collection.binance.enabled` and the collector is installed (default `enabled: false`); `server.reset_rate_limiter()` for deterministic tests.

### WP-1 — Dashboard
- **Static SPA** mounted at `/dashboard/` (root `/` 307-redirects there): API-key sign-in, live SSE event log, workflow advance, kill-switch, strategy catalog (create/enable/disable/promote/archive), positions/orders/trades tables, equity-curve canvas.
- **Packaging**: `[tool.setuptools.package-data]` ships `*.html`/`*.js`/`*.css` in wheels.

### WP-2 — Live-trading verification / dry-run
- **`LiveReadinessService`** (`domain/services/live_readiness.py`): verdict over broker connectivity/balance, data feeds, kill-switch, live preflight, operator-session state; exposed via `GET /v1/live/check`.
- **Workflow dry-run**: `dry_run: bool` on workflow advance lets operators rehearse the `controlled_live` transition without enabling live execution (`live_execution_enabled` surfaced in the verdict and gate result).

### WP-5 — Ops polish
- **`tests/conftest.py`** autouse rate-limiter reset → randomized-order full suite is deterministic.
- Lint/typecheck cleanup: duplicate `WorkflowAdvanceRequest` fields removed (PIE794), unused imports removed (F401), redundant comparison simplified (reportUnnecessaryComparison).

### Verification
- **1031 tests passing, 1 skipped** (full suite, repeated runs), **86.80% coverage** (threshold 70%), **ruff 0 errors** and **pyright 0 errors** on all changed files. Sprint report: `docs/sprints/SPRINT_16.md`.

## [Unreleased] - Sprint 15 (Deployment, Railway, Maintenance/Release)

### Deployment
- **Compose stack** (`docker-compose.yml` rewritten): `postgres` (16-alpine, healthchecked), `traderos-api` (PG-backed, healthchecked via `/v1/healthz`), `traderos-daemon` (paper mode, 60s interval), `postgres-test` (test profile). `docker compose config -q` clean.
- **PostgreSQL migrations-on-boot**: fresh-PG path fixed end-to-end — v001 `SERIAL PRIMARY KEY`, `BOOLEAN DEFAULT TRUE`, obsolete legacy `strategies`/`backtest_results` tables removed; v006 `_serial(backend)` + unified legacy strategy rebuild; `db check` cursor fix. Fresh PG migrates to Schema version 6.
- **Railway**: Dockerfile `VOLUME` removed (unsupported by Railway) in favor of `railway.toml` volumes (`/app/data`, `/app/exports`), healthcheck `/v1/healthz`, `startCommand`. API binds `$PORT` (default 8000). Deployment live at `traderos-production.up.railway.app` with `Postgres-gKbz` service and `DATABASE_URL` wired.
- **CI `deploy-check` job**: compose validation, fresh-PG migration smoke (Schema version 6), API container health smoke.

### Maintenance / Release
- **Single version source**: `pyproject.toml` (`1.1.0`) is authoritative; dead `VERSION` file removed; `settings.yaml` synced; CI `version-check` job guards drift.
- **`release.yml`**: tag-triggered (assert tag == package version, full test gate, sdist/wheel, GHCR image with semver tags, GitHub Release from CHANGELOG section).
- **Secret rotation**: `SecretRotator` (env provider) wired into the orchestrator lifecycle and surfaced in `get_status()`.
- **Retention**: `order_events` journal purged via `applied_at` in `purge_old_entries`; file logging uses `RotatingFileHandler` (`LOG_MAX_BYTES`, `LOG_BACKUP_COUNT`).

## [Unreleased] - Sprint 14 (Programme C — Commercial Surface)

### C2 — Enforced operator workflow
- **`OperatorWorkflow`** (`domain/services/operator_workflow.py`): 10-step canonical lifecycle (start → preflight → broker_check → market_data_check → paper_trading → performance_review → strategy_promotion → controlled_live → shutdown → session_report). Strict ordering: only the immediate next step or a re-run of the current one; out-of-order attempts raise `WorkflowError`.
- **`OperatorSessionService`** (`domain/services/operator_session.py`): every step gated on a real check — preflight verdict, broker balance + state reconciliation, market-data feed count, running paper sessions, catalog comparison ranking, strategy promotion, live-mode preflight, paper shutdown. Failing gates return `ok=False` (no advance); successful transitions are persisted through `OperatorWorkflowRepository`.

### C3 — Strategy catalog
- **`StrategyCatalogService`** (`domain/services/strategy_management.py`): seeded built-in templates (moving_average_trend, volatility_breakout, mean_reversion), versioned strategies, lifecycle (draft → active → disabled/archived, single promoted), clone, backtest comparison ranking, review.
- Execution loop consumes only enabled strategies via the `enabled_strategies` callable bound in the orchestrator.

### C1 — Operator API
- **`register_operator_endpoints`** (`interfaces/api/operator.py`), served under `/v1`: read panels (positions, orders, trades, portfolio, equity-curve, pnl, kill-switch, preflight, readiness, workflow, strategies, review) and write actions (kill-switch engage/disengage, workflow advance, strategy create/compare/enable/disable/promote/archive/clone). Error semantics: 400/404/409/501.

### C4 — Session reports
- **`SessionReportService`** (`domain/services/session_report.py`): immutable session snapshot (workflow state, transition log, portfolio, positions, trades, catalog + promoted strategy, risk, duration) with JSON and Markdown exports.
- Endpoints: `GET /v1/reports/session` and `?fmt=markdown`.

### C5/C6 — Documentation and productization
- **`docs/engineering/FINISH_LINE_DASHBOARD.md`** (new): authoritative operator-surface design doc (workflow semantics, endpoint map, error semantics, catalog, report contract, DoD).
- **`README.md`**: productized entry point — new features, operator curl examples, documentation table.

## [Unreleased] - Sprint 13 (Programme B — Operational Trust)

### OT-001 — Binance WebSocket transport (thin; live connectivity = declared risk)
- **`BinanceStreamTransport`** (`infrastructure/market_stream.py`): subscribes to `<symbol>@aggTrade`, parses frames, yields normalized raw ticks. Pure `parse_trade_frame` (handles combined-stream envelopes + raw `aggTrade`/`trade`, skips acks/klines), `build_subscription_frame`, `binance_stream_symbol`. Connector injected for offline tests; default lazily imports `websockets`. Live connect is **not** claimed — no network in this environment.

### OT-002 — Durable idempotency/replay + restart recovery
- **`OrderEventJournal`** (`infrastructure/journal.py`, `v005` migration): durable `order_events` table; preloads processed IDs at startup; `replay()` republishes pending events.
- **`DurableRunManifest`** (`infrastructure/run_manifest.py`): sqlite-backed run history + `detect_unclean_shutdown`; `DaemonController._detect_crash`/`_recover_from_crash` run post-crash reconciliation only after an unclean shutdown.

### OT-003 — Order-event side effects atomic via outbox
- **`OrderEventEngine`**: journal record committed **before** persist/publish; `mark_published` only after a successful publish; publish failures stay pending and replay exactly once.

### OT-004 — Tick validation + timestamp normalization
- **`validate_tick`/`normalize_timestamp`/`InvalidTickError`** (`market_stream.py`): finite positive price, non-negative quantity, symbol checks, ms-vs-seconds auto-detection, stale/future rejection; malformed frames skipped, counted, never treated as a transport outage.

### OT-005 — ACKNOWLEDGED open-order parity + Postgres migration path (H7)
- **`OPEN_TRADE_STATUSES`** in `domain/entities/trade.py` used by in-memory/sqlite/postgres `get_open()`.
- **`migration_utils.execute`** cursor routing; version marker deleted **before** `down()`; `v002–v005` backend-aware + idempotent; `v004` guards a missing `trades` table on fresh PG.

### OT-006/OT-011 — Concurrency safety
- **Per-trade locks** in `OrderEventEngine` (64/32-thread tests: exactly-one acceptance). **`ThreadSafeSQLiteConnection`** (`infrastructure/database/connection.py`) serializes every statement/cursor; `_connect_sqlite` honors an explicit `Config.db_path` (env must not shadow it).

### OT-007/OT-008 — Candle robustness + bounded retention
- **`CandleAggregator`**: epoch buckets, `flush`/`flush_all`/`flush_stale`, late-tick rejection + counter, bounded closed-bucket deque. **`ReplayRecorder`**: maxlen deque + drop counter; latency buffer trimmed.

### OT-009 — Duplicate/overflow fill guards
- **`_validate_fill`**: rejects non-finite/≤0 quantity, quantity > order quantity, non-finite/≤0 price.

### OT-010 — Bounded health + liveness/readiness
- **`run_with_timeout`** (`infrastructure/health.py`); `GET /v1/healthz` (liveness, no orchestrator build) and `GET /v1/health` (readiness, bounded by `ORCHESTRATOR_READY_TIMEOUT`, 503 degraded on timeout) in `interfaces/api/server.py`.

### Regression surface
- **`tests/test_programme_b_operational_trust.py` (new, 51 tests)** covering all 11 findings.

### Docs
- **`docs/engineering/OPERATIONAL_TRUST_MATRIX.md`**, **`docs/engineering/RECOVERY_TRUTH.md`**, **`docs/engineering/FAILURE_INJECTION_REPORT.md`** (new); MEP §26 and blueprint §13/§14 updated. Sprint report: `docs/sprints/SPRINT_13.md`.

### Verification
- **864 tests passing, 0 failures**, **83.77% coverage** (threshold 70%), **ruff clean on `src/traderos`**, **pyright 0 errors**.
- **Declared, non-fabricated remaining risks:** R-01 live Binance WS connectivity (no network/`websockets` in sandbox); R-02 live Alpaca/Postgres behavior (no credentials/server). Both are contract/structure-tested only.

## [Unreleased] - Sprint 12 (Programme A — Core Loop Integrity)

### D1/D2 — Fills now create positions; paper-broker fills no longer crash
- **`CycleExecutor.run()` routes every accepted fill through `PortfolioService.fill_trade`** (`application/cycle_executor.py`): the only method that creates/updates `Position` rows. Previously the executor's `open_trade → submit → fill → update_trade` sequence left the position repo untouched (D1).
- **`fill_trade` handles the no-external-order-id case** (`domain/services/portfolio_service.py`): a PENDING trade without an order id is auto-submitted as `auto-{trade.id}` before filling, fixing the `PENDING→FILLED` `InvalidTradeTransitionError` caused by `PaperBrokerAdapter` returning `order_id=""` (D2). The raw state machine still rejects `PENDING→FILLED`; the fix routes *through* it.

### D3 — `size_position` returns shares, not dollars
- **`PortfolioService.size_position(cash, confidence, price)` now returns share quantity** (`round(cash * alloc / price, 8)`; `price <= 0 → 0.0`). Both callers (`cycle_executor.py`, `paper_trading_service.py`) pass `price=close_price`.

### D4 — Realized PnL reaches the kill switches
- **`PortfolioService` gains a `risk_service` field; `close_position` reports realized PnL** via `risk_service.record_realized_pnl`, which forwards to `KillSwitch` and `PersistentKillSwitch`. Wired in the composition root (`application/factory.py`).

### D5/D8/D9 — Strategies can fire; real market data and ATR reach the cycle
- **Cycle supplies the full real indicator set** to every strategy's `MarketState`: `sma_20/50`, `bb_upper_20/lower_20`, `atr_14`, and real `high`/`low`/`volume` from `candles[-1]` — so all registered built-in strategies can evaluate. Fallbacks to fabricated values occur only when candles are empty.
- **`assess_trade` receives the real computed ATR** instead of `close_price * 0.01`.

### D6 — Cycle metrics are truthful
- **`cycles.completed` counted exactly once per cycle** (was per-strategy); **`cycle.duration_ms` records the measured duration** (was ≈ 0).

### D7 — Double preflight retained by design
- Reclassified as **by-design** (TOCTOU re-check required by `test_preflight_execution_integration.py`). No code change.

### Regression surface
- **`tests/test_core_loop_invariants.py` (new, 11 tests):** pins invariants I1/I2/I3/I5/I6/I8/I9 and the D1–D6/D8/D9 closes.
- **`tests/test_cycle_executor.py`, `tests/test_portfolio_service.py`:** updated for realistic mocks and share-semantics sizing.
- **Docs:** `docs/engineering/CORE_LOOP_TRUTH.md` (execution graph + defect register), `docs/engineering/CORE_LOOP_EVIDENCE.md` (per-defect proofs), `docs/AUDIT_GROUND_TRUTH.md` committed.

### Verification
- **843 tests passing, 0 failures** (`python3 -m pytest -q -p no:randomly`), **84.63% coverage** (baseline 84.42%), **ruff clean on `src/traderos` + touched tests**, **pyright 0 errors**. Sprint report: `docs/sprints/SPRINT_12.md`.

## [Unreleased] - Sprint 11 (Programme Ω — Operational Verification)

### Ω.1 — Audit Integrity (GATE 1)
- **`verify_chain()` content-integrity fix** (`infrastructure/audit.py`, `observability.py`, `observability_postgres.py`): All 3 backends now recompute each entry's expected hash from field values and compare against stored hash, plus verify previous_hash link integrity. Tampering with any of the 7 auditable fields (id, action, actor, resource, detail, timestamp, previous_hash) is detected.
- **Six-field mutation tests**: Individual mutation tests for action, actor, resource, detail, timestamp, previous_hash in both InMemory and SQLite backends. Single-entry and broken-link tamper tests.
- **Multi-seed PYTHONHASHSEED verification**: SHA256 hash computation proven identical across seeds 0,1,42,12345,99999 via subprocess isolation.
- **ADR-008 updated**: Status changed to "Accepted", verify_chain() behavior now accurately documented, hash recomputation verified in all backends.

### Ω.2 — Broker Reconciliation (GATE 2)
- **Full 10-mismatch detection engine** (`domain/services/broker_state_reconciliation_service.py`): MismatchType enum with broker-only/local-only positions and orders, quantity mismatch, price mismatch, stale snapshots, duplicate broker state, broker failures, unknown state.
- **Each mismatch wired to KillSwitch** (severity >= 2 increments consecutive_failures), **health** (report_unhealthy per mismatch), **audit** (reconciliation.mismatch entry), **metrics** (per-mismatch-type counter + reconciliation.mismatches total).
- **DaemonController** passes local state to reconciliation, records audit entries and metric counters for all mismatch types.
- **14 tests** (5 legacy updated + 9 new: all 10 mismatch types proven via integration test).

### Ω.2b — PreflightService (GATE 2b)
- **PreflightService wired into production path**: Created in `build_orchestrator()` factory with audit + broker_reconciliation + kill_switch dependencies; passed through TradingOrchestrator to both DaemonController (as pre_cycle_hook) and CycleExecutor (as pre-submission gate).
- **Every refusal condition independently prevents live order submission**: PreflightService.check() called at start of each signal's trading loop in CycleExecutor.run() before broker.place_market_order().
- **Spy/mock tests proving broker.send is never called when preflight fails**: 4 integration tests verifying that preflight failures (general, blocked reconciliation, engaged kill switch) all prevent broker.place_market_order from being invoked.

### Ω.4 — Operational Recovery
- **Timed backup/restore tests**: Backup and restore both complete within 5-second SLO.
- **Crash recovery drill tests**: Simulated crash with order reconciliation, kill-switch reset after recovery, broker outage recovery, preflight re-pass after recovery.
- **Reconciliation drill tests**: Full reconciliation cycle with matched state, full recovery after mismatch fix.
- **recover_from_crash()** updated: accepts local_trades and broker_orders_state parameters for actual state reconciliation.

### Rate-limiter wrapper (Programme C)
- **Rate-limited broker adapter** (`infrastructure/broker_rate_limiter.py`): Flagged `BrokerAdapter` proxy. Disabled by default (`BROKER_RATE_LIMIT_ENABLED`).

### Operations runbooks (Programme C)
- **Operations runbook**, **Controlled-pilot parameters**, **Cold incident drill**, **Deployment rollback drill**.

### L1 — Healthy-Overwrite Bug Fix
- **`_handle_reconciliation_result` fix** (`daemon_controller.py`): Removed `report_healthy("broker_reconciliation")` from mismatch branch. When mismatches exist, only `report_unhealthy` is called. `report_healthy` only called from the no-mismatch path.

### L2 — Stale-Snapshot Severity Raised
- **`MismatchType.STALE_SNAPSHOT` severity 1→2** (`broker_state_reconciliation_service.py:217`): Now trips KillSwitch, increments metric counter, and blocks order acceptance.

### L3 — PostgreSQL Audit Chain Ordering Fix
- **`id_seq SERIAL` column added** to `audit_log` table (`v002_observability.py`): PostgreSQL `verify_chain()` was using `ORDER BY id` on UUID text column, which sorts alphabetically not by insertion order. Fixed all 4 ORDER BY clauses in `observability_postgres.py` to use `id_seq`.
- **8 PostgreSQL mutation tests** (`test_observability_postgres.py`): All 6 field mutations (action, actor, resource, detail, timestamp, previous_hash) + broken link + untampered chain. Fresh-connection fixture eliminates cursor-visibility races. 8/8 pass.

### L4 — Dependency Direction Fitness Test
- **Committed fixture** (`_fixture_broken_domain.py`): Deliberate infrastructure import in domain proves AST checker catches violations. Tested in `test_dependency_direction.py`.

### L5 — 60-Assertion Effect Matrix
- **`test_reconciliation_effects.py`**: Parametrizes all 10 mismatch types × 6 effects (detection, health, kill-switch, audit, metrics, notifications) + 3 regression tests. ~63 assertions.

### L6 — 10 Preflight Refusal Tests + TOCTOU
- **Expanded 4→10 tests** (`test_preflight_execution_integration.py`): All refusal conditions + TOCTOU race test.
- **TOCTOU protection** (`cycle_executor.py`): Re-checks preflight right before `broker.place_market_order()`.

### L7 — Operational Recovery Logs
- **Backup/restore logging** (`backup.py`): `logger.info()` with timestamps for `backup_sqlite()` and `restore_sqlite()`.
- **3 log-capture tests** (`test_operational_recovery.py::TestRunbookExecution`): Backup log, restore log, full workflow with data verification.

### L8 — Clean Ship
- **Lint zero**: `ruff check src/traderos/` — 0 errors.
- **All tests green**: 832 passing, 0 failures (was 801 + 1 pre-existing failure now fixed).
- **`TradeStatus.ACKNOWLEDGED`** + `Trade.acknowledge()` added for Sprint 9 test compatibility.

### Governance
- **ADR-008**: Updated to Accepted status, verify_chain() behavior now matches implementation exactly.
- **SPRINT_11.md**: Programme Ω complete — all 9 Codex rejection points resolved across 8 layers.
- **832 tests passing, 0 failures, 0 lint errors.**

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
- **KillSwitch metrics**: `RiskService` accepts optional `MetricsPort`; kill-switch trips increment the `circuit_breaker.tripped` counter for operational visibility.
- **Manual-reset-only circuit breaker**: `KillSwitch` and `PersistentKillSwitch` both enforce manual-reset semantics. Removed dead `circuit_open_until` field and cooldown-based auto-reset logic from `PersistentKillSwitch`.
- **Coverage threshold**: `pyproject.toml` `fail_under = 70` documented as MEP §17 interim gate with path to 90%.

### WP-10.1 — Audit Chain: SHA256 over Canonical Serialization
- **ADR-008 ratified**: Replaced non-deterministic `hash()` with `hashlib.sha256()` over canonical JSON serialization. Fixes pipe-delimiter ambiguity bug. Pre-fix chain boundary documented — old entries not retroactively rehashed.
- **Shared `compute_audit_hash()`** in `infrastructure/audit.py` used by all three backends (InMemory, SQLite, PostgreSQL).

### WP-10.2 — Broker State Reconciliation
- **`BrokerStateReconciliationService`** (`domain/services/broker_state_reconciliation_service.py`): Periodically reconciles broker positions and open orders against local state. Blocks order acceptance until first successful startup reconciliation.
- **Reconciliation failures trip KillSwitch**: `record_failure()` called on each error, NOT just logged.
- **`get_open_orders()`** added to `BrokerPort` protocol and all adapters (`PaperBrokerAdapter`, `AlpacaBrokerAdapter`).
- **DaemonController** runs startup + periodic reconciliation; skips trading cycles when `can_accept_orders` is False.

### WP-10.3 — Preflight Go/No-Go Gate
- **`PreflightService`** (`domain/services/preflight_service.py`): Composes audit-chain verification + reconciliation freshness + kill-switch state + live-mode confirmation into a single `PreflightVerdict`.
- **`PreflightVerdict`**: Named tuple with `passed`, `checks` dict, `failures` list, and `timestamp`. Truthy on pass, falsy on fail.
- **Live mode gate**: Requires `LIVE_TRADING_CONFIRMED=true` environment variable as explicit confirmation beyond basic env-var presence.

- **750 tests passing at 81%+ coverage.**

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

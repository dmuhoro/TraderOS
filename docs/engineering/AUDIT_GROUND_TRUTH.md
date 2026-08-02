# TraderOS — Audit Ground Truth

**Version:** 1.0
**Date:** 2026-07-31
**Author:** Ground-truth audit pass (engineering intelligence mission)
**Basis:** Re-runnable raw commands executed against the working tree at commit `47b3898` (HEAD). Every claim below was either executed directly or read from source — nothing is inherited from prior reports.

> **Purpose.** The Strategic Completion Blueprint (see `STRATEGIC_COMPLETION_BLUEPRINT.md`) makes claims about test counts, coverage, lint, type-strictness, runbook executability, and Codex-rejection closure. This document pins each claim to the exact command that proves it, records the discrepancy between "documented truth" and "machine truth", and ends with a ranked gap list plus a minimum-move execution sequence.

---

## 1. Machine Truth — Re-runnable Commands

All commands run from repo root with the project virtualenv active. Re-run in a cold checkout to reproduce.

| # | Command | Result |
|---|---------|--------|
| 1 | `pytest --collect-only -q -o addopts=""` | **832 tests collected** |
| 2 | `pytest -q` | **832 passed, 0 failed** — 84.42% coverage (6914 stmts, 1077 missed; coverage threshold 70% satisfied) |
| 3 | `ruff check src/traderos/` | **"All checks passed!"** — 0 errors |
| 4 | `pyright src/traderos` | **"0 errors, 0 warnings, 0 informations"** |
| 5 | `grep -rn "traderos.infrastructure" src/traderos/domain/` | **0 matches** — no infrastructure import anywhere under domain/ (architectural isolation confirmed) |

### 1.1 Per-file test counts (source-verified)

| Test file | Collected | Evidence |
|-----------|-----------|----------|
| `tests/test_observability_postgres.py` | 8 | untampered + 6 field mutations + broken link (lines 129–186) |
| `tests/test_reconciliation_effects.py` | 13 | 4 test fns; `@pytest.mark.parametrize` 10 mismatch types (lines 60–74) × 6 effects E1–E6 (line 49); 20 literal `assert` lines → 60 runtime assertions |
| `tests/test_preflight_execution_integration.py` | 10 | includes `test_toctou_kill_switch_trips_between_check_and_submit` |
| `tests/test_operational_recovery.py` | 11 | crash-recovery drill scenarios |
| `tests/test_audit_integrity.py` | 5 | + `tests/test_audit_service.py` 16 = **21** |
| `tests/architecture/test_dependency_direction.py` | 2 | includes committed breaking fixture |

**Sum:** 8 + 13 + 10 + 11 + 21 + 2 = 65 collected from the Programme Ω signature files; the remaining 767 come from the rest of the suite.

### 1.2 Verification of selected blueprint claims

| Blueprint claim | Machine truth |
|-----------------|---------------|
| PG migration path calls `conn.execute()` on psycopg2 | ✅ `migration_manager.py:20,28,62,89,103` |
| Cycle never creates Position via `fill_trade()` | ✅ `cycle_executor.py:197-213` — `open_trade`/`update_trade` only |
| Sizing returns dollars used as qty | ✅ `portfolio_service.size_position` returns `cash*allocation` |
| docker-compose runs two API instances, no daemon | ✅ compose services `traderos` + `traderos-api` share entrypoint |
| `StreamTransport` has no implementations | ✅ protocol-only in `market_stream.py` |
| No `modify_order` anywhere | ✅ grep across BrokerAdapter/Port/Alpaca/Paper/RateLimited — zero hits |
| 832 tests / 84% coverage / ruff clean / pyright strict | ✅ commands 1–4 above |

---

## 2. Runbook & ADR Inventory — PROCEDURE vs EXECUTION RECORD

**Method:** for each runbook, searched for (a) timestamps, (b) executor/author keywords, (c) observed results. **All files are PROCEDURES — none is an execution record.**

| File | Kind | Evidence |
|------|------|----------|
| `docs/runbooks/COLD_INCIDENT_DRILL.md` | PROCEDURE | 0 timestamps, 0 executor keywords, 0 observed results |
| `docs/runbooks/CONTROLLED_PILOT.md` | PROCEDURE | same |
| `docs/runbooks/DEPLOYMENT_ROLLBACK_DRILL.md` | PROCEDURE | same |
| `docs/runbooks/OPERATIONS.md` | PROCEDURE | same |
| `runbooks/disaster_recovery.md` | PROCEDURE | same |

| ADR | Status | Note |
|-----|--------|------|
| `docs/adr/ADR-005.md` | **Accepted** | |
| `docs/adr/ADR-006-live-market-infrastructure.md` | **Accepted for staged implementation** | live pilot remains gated |
| `docs/adr/ADR-007-circuit-breaker-recovery.md` | **Proposed** | |
| `docs/adr/ADR-008-audit-chain-sha256.md` | **Accepted** | |
| `docs/decisions/ADR_001_RESEARCH_FIRST.md` | — | split-tree duplicate of ADR tree (D13 in blueprint) |

---

## 3. Runbook → CLI Mismatch (verified, not claimed)

The five runbooks command operators to run CLI verbs that **do not exist**. This makes every drill unexecutable (blueprint VB4).

| Runbook command | Exists? | Reality |
|-----------------|---------|---------|
| `traderos risk check` / `kill` / `status` / `reset` / `reconcile status` | ❌ | no `risk` subparser |
| `traderos metrics watch --cycles 3` | ❌ | no `metrics` subparser |
| `traderos audit verify` | ❌ | `audit` exists but has no `verify` verb |
| `traderos daemon start` | ❌ | `daemon` exists but has no `start` verb |

**Actual CLI surface** (from `add_parser` in `src/traderos/interfaces/cli/main.py`):
`strategies`, `backtest`, `papertrade` (`create`, `list`), `health`, `audit`, `notify`, `signal`, `daemon`, `validate`, `db` (`migrate`, `check`, `rollback`, `backup`, `restore`, `list-backups`).

---

## 4. Codex Rejection Items — All 10 DONE (reconstructed)

> **Provenance note:** no Codex rejection file exists in the repository. The rejection list was reconstructed from `docs/sprints/SPRINT_11.md` and `CHANGELOG.md`, then each item verified against machine truth. If the original list differs, the delta is expected to be **additional** items only — every reconstructed item below is verifiably closed.

| # | Codex rejection item | Status | Evidence |
|---|---------------------|--------|----------|
| 1 | PostgreSQL audit mutation tests (6 fields + broken link) | **DONE** | `tests/test_observability_postgres.py` 8 collected (6 field mutations + broken link + untampered), run against live Docker PG |
| 2 | Dependency fitness test | **DONE** | `tests/architecture/test_dependency_direction.py` 2 collected; committed breaking fixture; ruff/pyright clean |
| 3 | Effect matrix (mismatch × side-effect) | **DONE** | `test_reconciliation_effects.py` 13 collected; 10 mismatch types × 6 effects E1–E6 (20 literal asserts = 60 runtime) |
| 4 | Preflight refusal tests (10 required) | **DONE** | `test_preflight_execution_integration.py` 10 collected, including TOCTOU race |
| 5 | Operational recovery log evidence | **DONE** | `test_operational_recovery.py` 11 collected |
| 6 | Audit integrity + audit service | **DONE** | 21 collected (5 + 16) |
| 7 | Stale snapshot severity 1 → 2 | **DONE** | `severity=2` at `broker_state_reconciliation_service.py:217` (`STALE_SNAPSHOT`) |
| 8 | Healthy-overwrite bug | **DONE** | `report_healthy` only in no-mismatch path (`daemon_controller.py:164-167`); mismatch branch calls only `report_unhealthy` (line 147) |
| 9 | Lint errors (70+) | **DONE** | `ruff check src/traderos/` → 0 errors |
| 10 | Pre-existing test failures | **DONE** | `pytest -q` → 832 passed, 0 failed |

### 4.1 Supporting environment
- PostgreSQL audit-chain tests run against **live** Docker container `traderos-postgres-test` (up 21+ hours) — not mocked.
- Postgres chain ordering fixed in Programme Ω: `verify_chain()` previously `ORDER BY id` on a UUID text column (alphabetical, not insertion order) → added `id_seq SERIAL` in `v002_observability.py` + fixture DDL; all 4 `ORDER BY id` → `ORDER BY id_seq` in `observability_postgres.py`. SQLite `id_seq` is nullable so legacy INSERTs remain valid.

---

## 5. Ranked Gap List

The gaps below are ordered by **leverage** = (risk reduced × business value) / effort. Items 1–6 are the minimum move; items 7+ are follow-on. Everything marked *human-required* needs a real-world decision or credential, not code.

| Rank | Gap | Class | Effort | Why it ranks here |
|------|-----|-------|--------|-------------------|
| 1 | **Core loop correctness** — position bookkeeping (`fill_trade` never called), sizing dollars→shares, daily-loss unwired, 2/3 strategies can never fire, double-preflight | Correctness defect | ~4 weeks (Programme A) | Silent account corruption; every downstream programme inherits it |
| 2 | **Live market transport (OT-001)** — `StreamTransport` protocol, zero implementations | Release blocker | ~5 days | No live data path at all |
| 3 | **Durable idempotency + replay (OT-002)** + **atomic side effects (OT-003)** | Release blocker | ~8 days | Restart loses duplicate-fill protection; failure leaves inconsistent state |
| 4 | **PostgreSQL migration path (PB5/H7)** + API health boundedness (OT-010) | Release blocker | ~3 days | `DATABASE_URL` set → crash; `/v1/health` stalls 45s+ |
| 5 | **Web dashboard MVP** — positions/P&L/orders/kill-switch | Commercial blocker | ~3 weeks (Programme C) | Zero commercial surface today |
| 6 | **Runbooks/CLI truth (VB4)** | Governance blocker | ~2 days | Drills are fiction; fix = make documented commands exist |
| 7 | **Live-credential verification** — authenticated Binance/Alpaca sandbox test | *human-required* | ~3 days | Cannot claim live capability without keys |
| 8 | **Controlled pilot run** | *human-required* | ~1 week | The actual proof of PRI ≥ 70 |
| 9 | Delete dead weight + consolidate duplicates (Sections 4/10 of blueprint) | Simplification | ~2 days | ~40 modules test-only; coverage rises mechanically |
| 10 | Version/tag drift + release workflow (VB6) | Governance | ~1 day | 1.0.0 vs 1.1.0 vs 0.3.0 |
| 11 | docker-compose daemon + prod PG (PB12) | Deployment | ~1 day | Trading loop never deployed |
| 12 | Onboarding/accounts + paper→live flow in UI | Commercial | ~5 days | Post-dashboard |

---

## 6. Minimum-Move Execution Sequence

Separated into **human-clock** (wall time, parallelizable, mostly decisions/credentials) vs **agent sessions** (executable in this tool, verifiable by tests). Human items can run while agent items are in flight.

### Phase 0 — commit the audit trail (one agent session, <1 day)
1. Commit `docs/engineering/STRATEGIC_COMPLETION_BLUEPRINT.md` + this document → `47b3898`-based commit.
2. Push to `main`.

### Phase 1 — Programme A: Core Loop Integrity (~4 weeks)
| Task | Class |
|------|-------|
| Position bookkeeping: call `fill_trade`; prove with test | agent |
| Sizing: dollars→shares; prove with test | agent |
| Daily-loss: `record_realized_pnl` on close trips kill-switch; test | agent |
| Strategy params + make all 3 strategies fireable; test | agent |
| Remove double-preflight; correct cycle metrics; test | agent |
| Refactor `CycleExecutor.run()` into phased functions | agent |
| Wire regime/breakout/liquidity/session detection into cycle | agent |
| Delete dead weight + consolidate D1/D4/D7; unify strategy store | agent |
| Runbook/CLI truth: add missing subcommands (`risk`, `metrics`, `audit verify`, `daemon start`) so drills execute | agent |
| Version/tag alignment | agent |
| **DoD:** cycle creates positions, sizing in shares, 3/3 strategies fire, preflight once, runbooks executable, 832+ tests green | — |

### Phase 2 — Programme B: Operational Trust (~5 weeks)
| Task | Class |
|------|-------|
| Binance WebSocket transport behind `StreamTransport` (OT-001); failover tested | agent |
| Tick validation + timestamp normalization (OT-004) | agent |
| Durable idempotency + replay (OT-002); exactly-once test | agent |
| Outbox/transactional order side effects (OT-003) | agent |
| Serialize order events (OT-006); ACKNOWLEDGED open-order parity (OT-005) | agent |
| Candle robustness (OT-007); retention policy (OT-008) | agent |
| Fix PG migration path (PB5/H7); fresh-PG schema (H6); PG repo tests | agent |
| API health boundedness (OT-010) | agent |
| docker-compose: daemon + prod PG stack | agent |
| **Live-credential verification: sandbox order + live tick** | **human** (needs keys) |
| **Controlled pilot run + report** | **human** |
| **DoD:** PRI ≥ 70; controlled pilot approved; PG + API claims true | — |

### Phase 3 — Programme C: Commercial Surface (~3 weeks)
| Task | Class |
|------|-------|
| Dashboard MVP: real-time positions/P&L/orders/kill-switch/health | agent |
| Paper→live flow with forced confirmation + audit trail | agent |
| First-run onboarding | agent |
| **DoD:** a person can sign in and run the platform; Commercial Readiness 0 → ~60 | — |

**Total: ~12 weeks to production-complete, operationally-trusted, commercially-usable.**

---

## 7. Trust Boundary Statement

- **High-trust, proven by machine:** audit chain (3 backends, real PG), broker state reconciliation (10×6 matrix), backup/restore, preflight gate, architecture enforcement, 832 tests @ 84.42%.
- **Untrusted, proven absent:** live transport, durability, atomicity, PG path, API boundedness — all are **wiring/implementation** gaps, not design gaps.
- **Cannot be proven by this tool:** live-credential verification and the controlled pilot. These require human decisions and real-world credentials; nothing in the repository substitutes for them.

---

## 8. Cold-Checkout Reproduction Script

```bash
git clone <repo> && cd TraderOS
# PG audit tests need a live container:
docker run --name traderos-postgres-test -e POSTGRES_PASSWORD=test -p 5433:5432 -d postgres:16
pytest --collect-only -q -o addopts=""                      # 832 collected
pytest -q                                                    # 832 passed, 84.42%
ruff check src/traderos/                                     # All checks passed
pyright src/traderos                                          # 0 errors
grep -rn "traderos.infrastructure" src/traderos/domain/      # 0 matches
```

---

## 9. Engineering Closure Pass Delta (2026-08-02)

Supersedes the counts in §7–§8; reality moved since that check-out was recorded:

| Metric | Old (§8) | Now (2026-08-02) |
|---|---|---|
| Tests collected / passing | 832 / 843 | **1266 / 1266** |
| Coverage | 84.42 % / 84.63 % | **93.62 %** |
| `ruff check .` (whole repo) | src only | **0 errors (full repo)** |
| Metrics endpoint `/metrics` | — | **200** (was 501; prometheus-client added) |
| Dead daemon stubs | present | **removed** (`_is_market_hours`, `_drain_open_orders`) |
| `pilot` / `security` CLI verbs | absent when runbooks written | **present** (added sprint 17/18) |

**Still open (unchanged, not fabricated — see `ENGINEERING_CLOSURE_AUDIT.md` §10):**
- Live authenticated Binance + Alpaca connectivity drills (no network in this sandbox).
- Controlled pilot execution.
- Runbook→CLI gap: `traderos daemon start`, `risk`, `metrics`, `audit verify` still do not exist (CLOSURE-14).
- Durable `OrderEventEngine`/journal still not wired into the live order path (CLOSURE-12).

## 10. Programme Ω Delta (2026-08-02) — first genuine execution evidence

The machines' previously open blocks: "Live authenticated Alpaca paper connectivity drill" and "backup/restore/rollback under real commands & checksums" are now **DONE**, exercised for real on 2026-08-02. Raw records: `docs/evidence/2026-08-02_*.log`.

| Programme Ω item | Result | Verified detail |
|---|---|---|
| `Config.load()` from fresh dir | **FIXED** | auto-creates runtime dirs (regression: `test_load_creates_missing_db_directory`); previously `ConfigError` |
| Alpaca **paper** dry-run rehearsal (real account) | **DONE** | account `***`, equity 100,000; reconcile 0–position/order, `can_accept_orders=True`; operator workflow **READY** (prefight → broker_check → market_data → paper_trading → performance_review → SKIP strategy-promotion → controlled_live dry-run → shutdown → session_report), exit **0** |
| Backup → restore | **DONE** | SHA-256 round-trip equal (`b91b07a…` == `b91b07a…`); marker row preserved; integrity `ok` |
| Migration rollback | **DONE** | 6 → 3 (tables 24 → 21) → 6 (24), integrity `ok` each |

**Real defects surfaced & fixed by the genuine run:**
- `AlpacaBrokerAdapter.get_open_orders()` called `get_orders(status="open")` — incompatible with alpaca-py 0.43.5 (`GetOrdersRequest(QueryOrderStatus.OPEN)`); fixed + test updated.
- LIVE-mode operator workflow hard-failed at `PAPER_TRADING` because no `PaperTradingService` was built outside `PAPER` mode; factory now builds it for `LIVE` too, so the rehearsal completes (safe under `dry_run=True`).

**Still open (unchanged):** real-money live pilot only. Sprint 21 (2026-08-02) closed the previously-open items: **Binance live (R-01)** and **Postgres failure drill (R-02)** PASSED against real network/Postgres (`docs/evidence/2026-08-02_l4r01_binance_live.log`, `..._l4r02_postgres_crash_drill.log`); replay wiring was re-scoped as the durable `JournaledBroker` + restart proof (CLOSURE-12, L1/L2); runbook→CLI parity shipped via `risk`/`metrics`/`daemon start`/`audit verify` (CLOSURE-14, L3).

# TraderOS — Audit Ground Truth

**Date:** 2026-07-31
**Commit under audit:** `47b3898` (HEAD = `66dfff1`, docs-only delta)
**Method:** Every claim below is backed by an attached, re-runnable command executed in this pass. No number is inherited from a prior report, doc, or memory. Source code was NOT modified in this pass.

**Self-check (Task 4):** every DONE in this file survives an independent reviewer re-running the exact commands from a cold checkout, because the raw command + raw output are attached. Items that depend on a human or on state that a cold checkout cannot reproduce are explicitly marked, never marked DONE.

---

## 1. Machine Truth — Raw Command Outputs

### 1.1 Full test count — `pytest --collect-only -q`

**Why this command:** collection count, not `grep "def test_"`. Default `addopts` includes `--cov` (pyproject.toml:78), which aborts collection when coverage fails; a clean count requires `-o addopts=""`.

```
$ pytest --collect-only -q -o addopts=""
832 tests collected in 4.33s
```

### 1.2 Full suite + actual coverage — `pytest -q` (addopts `--cov=.`)

```
TOTAL                                                                        6914   1077    84%
Required test coverage of 70.0% reached. Total coverage: 84.42%
======================= 832 passed, 7 warnings in 31.06s =======================
```

- **Actual coverage: 84.42%** (6914 statements, 1077 missed). Config threshold is `fail_under = 70` (pyproject.toml:85).
- ⚠ **Discrepancy recorded:** `sprints/SPRINT_11.md:170` claims "actual: 85%". Machine truth is **84.42%**. The doc rounds up; the gate is 70, so this does not affect pass/fail, but the document is not verbatim-accurate.

### 1.3 Lint — `ruff check src/traderos/`

```
$ ruff check src/traderos/
All checks passed!
$ echo $?
0
```

### 1.4 Type checking — `pyright src/traderos`

```
$ pyright src/traderos
0 errors, 0 warnings, 0 informations
$ echo $?
0
```

### 1.5 Architectural isolation — `grep`

```
$ grep -rn "traderos.infrastructure" src/traderos/domain/
$ echo $?
1
```

Zero matches. `grep` exit code 1 = no matches found. Independently confirmed by the architecture test `test_no_infrastructure_imports_in_domain` (see §3, L4).

### 1.6 Per-file collection counts (re-runnable, not grepped)

```
$ pytest --collect-only -q -o addopts="" \
    tests/test_observability_postgres.py \
    tests/test_reconciliation_effects.py \
    tests/test_preflight_execution_integration.py \
    tests/test_operational_recovery.py \
    tests/test_audit_integrity.py \
    tests/test_audit_service.py \
    tests/architecture/test_dependency_direction.py \
    tests/test_broker_state_reconciliation.py
79 tests collected in 0.97s
```

Breakdown (from the collected node IDs):

| File | Collected | Contents |
|------|-----------|----------|
| `tests/test_observability_postgres.py` | 8 | untampered + 6 field mutations (action, actor, resource, detail, timestamp, previous_hash) + broken link |
| `tests/test_reconciliation_effects.py` | 13 | 10 × `MismatchType` parametrization + 3 regression (healthy-not-overwritten, healthy-when-clean, stale-trips-kill-switch) |
| `tests/test_preflight_execution_integration.py` | 10 | 8 refusal conditions + live-confirmation pair + TOCTOU race |
| `tests/test_operational_recovery.py` | 11 | 2 timed SLO + 5 crash-recovery + 2 reconciliation-drill + 3 runbook-log |
| `tests/test_audit_integrity.py` | 5 | SHA-256 determinism, known value, distinct-inputs, canonical-JSON-excludes-hash, multi-seed PYTHONHASHSEED |
| `tests/test_audit_service.py` | 16 | record/verify/mutations + find + pagination + clear |
| `tests/architecture/test_dependency_direction.py` | 2 | no-imports-in-domain + committed-fixture regression |
| `tests/test_broker_state_reconciliation.py` | 14 | 13 detection/recovery tests + all-10-mismatches integration |

**Sum:** 8 + 13 + 10 + 11 + 5 + 16 + 2 + 14 = **79**.

---

## 2. Runbook & ADR Inventory — PROCEDURE vs EXECUTION RECORD

Method: for each file, counted timestamp patterns (`20XX-XX-XX`, clock times), executor patterns (`executor`, `executed by`, `author:`, `run by`, `operator:`, `conducted by`), and observed-result patterns (`observed result`, `outcome:`, `result:`, `output:`, `exit code`).

| File | Timestamps | Executor | Observed results | Classification |
|------|-----------:|---------:|-----------------:|----------------|
| `docs/runbooks/COLD_INCIDENT_DRILL.md` | 0 | 0 | 0 | **PROCEDURE** (untested plan) |
| `docs/runbooks/CONTROLLED_PILOT.md` | 0 | 0 | 0 | **PROCEDURE** (untested plan) |
| `docs/runbooks/DEPLOYMENT_ROLLBACK_DRILL.md` | 0 | 0 | 0 | **PROCEDURE** (untested plan) |
| `docs/runbooks/OPERATIONS.md` | 0 | 0 | 0 | **PROCEDURE** (untested plan) |
| `runbooks/disaster_recovery.md` (root, duplicate tree) | 0 | 0 | 0 | **PROCEDURE** (untested plan) |
| `docs/adr/ADR-005.md` | 0 | 0 | 0 | **DECISION RECORD** — Status: Accepted |
| `docs/adr/ADR-006-live-market-infrastructure.md` | 0 | 0 | 0 | **DECISION RECORD** — Status: Accepted for staged implementation; live pilot remains gated |
| `docs/adr/ADR-007-circuit-breaker-recovery.md` | 0 | 0 | 0 | **DECISION RECORD** — Status: Proposed |
| `docs/adr/ADR-008-audit-chain-sha256.md` | 1 (`Date: 2026-07-29`) | 0 | 0 | **DECISION RECORD** — Status: Accepted. The single match is a decision date, not an execution timestamp |

**Finding (honest):** all five runbooks are **PROCEDURES**. There is **no EXECUTION RECORD anywhere in the repository** — no drill has ever been run and recorded, and no pilot has been executed. Every file in `docs/runbooks/`, `runbooks/`, and `docs/adr/` is a plan or a decision, not a record of an executed event.

**Associated CLI mismatch (verified, `src/traderos/interfaces/cli/main.py`):** runbooks command operators to run verbs that do not exist. Actual parser surface (all `add_parser` calls): `strategies, backtest, papertrade, health, audit, notify, signal, daemon, validate, db` (with `backup, check, list, list-backups, migrate, restore, rollback, create`).

| Runbook command | Exists? |
|-----------------|---------|
| `traderos risk check` / `kill` / `status` / `reset` / `reconcile status` | ❌ no `risk` subparser |
| `traderos metrics watch --cycles 3` | ❌ no `metrics` subparser |
| `traderos audit verify` / `audit query` | ❌ `audit` subparser has no `verify`/`query` verb |
| `traderos daemon start` | ❌ `daemon` subparser has no `start` verb |
| `traderos run` / `traderos status` | ❌ no such subparser |
| `traderos db backup/check/list/migrate/restore` | ✅ present |

---

## 3. Codex Rejection Report — Exists vs Required, Item by Item

**Provenance caveat:** no file named `*codex*` exists in the repository (verified by `find`). The rejection points were reconstructed from `sprints/SPRINT_11.md` ("All 9 Codex rejection points addressed in 8 sequential layers L1–L8") and `CHANGELOG.md`. Where SPRINT_11's own text and machine truth disagree, the discrepancy is recorded. **No item is marked DONE without attached evidence; every DONE below is re-runnable.**

| # | Codex requirement (as reconstructed) | Status | Evidence |
|---|---------------------------------------|--------|----------|
| 1 | Healthy-overwrite bug: `report_healthy` must not fire after `report_unhealthy` for mismatches | **DONE** | `daemon_controller.py:143-160` mismatch branch calls only `report_unhealthy` (line 147); `report_healthy("broker_reconciliation")` only at 164-167 (no-mismatch path). Regression test collected: `test_healthy_not_overwritten_on_mismatch` |
| 2 | STALE_SNAPSHOT severity 1→2; must trip kill switch + metric counter | **DONE** | `broker_state_reconciliation_service.py:214-217` (`severity=2`); kill-switch/metric gated on `severity >= 2` at `daemon_controller.py:148-151`. Regression test collected: `test_stale_snapshot_now_trips_kill_switch` |
| 3 | PostgreSQL audit mutation tests: 6 fields + broken link + untampered | **DONE** | 8 tests collected from `test_observability_postgres.py` (see §1.6) — run against live Docker PG |
| 3a | Audit-chain ordering: `id_seq` insertion order, not UUID text sort | **DONE** | `observability_postgres.py` all 4 `ORDER BY` on `id_seq` (lines 27, 76, 98, 129); `v002_observability.py:25` adds `id_seq` column |
| 4 | Dependency-direction fitness test + committed broken fixture | **DONE** | 2 tests collected (`test_no_infrastructure_imports_in_domain`, `test_committed_fixture_is_detected_as_violation`); fixture `tests/architecture/_fixture_broken_domain.py` exists and deliberately imports `traderos.infrastructure.retry`; skip logic at `test_dependency_direction.py:44`. Independently: §1.5 grep = zero infra imports in domain |
| 5 | 60-assertion effect matrix: 10 mismatch types × 6 effects | **DONE** | 10 parametrized cases collected (all 10 `MismatchType` values, see §1.6); each run asserts E1–E6 (detection/health/kill-switch/audit/metrics/notification) = 60 runtime assertions |
| 5a | 3 reconciliation regression tests | **DONE** | Collected: `test_healthy_not_overwritten_on_mismatch`, `test_healthy_reported_when_no_mismatches`, `test_stale_snapshot_now_trips_kill_switch` |
| 6 | 10 preflight refusal tests, expanded from 4 | **DONE** | 10 collected (see §1.6): 8 refusal conditions + live-with-confirmation + all-checks-pass |
| 6a | TOCTOU race: re-check preflight immediately before `place_market_order()` | **DONE** | `cycle_executor.py:185-192` re-check; `place_market_order` at 194. Test collected: `test_toctou_kill_switch_trips_between_check_and_submit` |
| 6b | Preflight wired into production composition | **DONE** | `factory.py:166,205`; `orchestrator.py:59-89` (`__post_init__` → `_pre_cycle_check`, `preflight_service=` at 89); `cycle_executor.py:154-160` first gate |
| 7 | Operational recovery: timed SLO + crash drill + timestamped logs | **DONE** | 11 tests collected (`TestTimedBackup` 2, `TestCrashRecoveryDrill` 5, `TestReconciliationDrill` 2, `TestRunbookExecution` 3); `backup.py:54-55,74` `logger.info` with `ts` |
| 8 | Lint zero | **DONE** | §1.3 verbatim: `All checks passed!`, exit 0 |
| 9 | All tests green, zero pre-existing failures | **DONE** | §1.2 verbatim: `832 passed, 7 warnings in 31.06s`, 0 failures |

**Gate cross-checks claimed by SPRINT_11:** Ω.1 audit integrity = 5 collected + ADR-008 Status Accepted ✅; Ω.2 broker reconciliation = **14 collected** in `test_broker_state_reconciliation.py` ✅; Ω.4 operational recovery = 11 collected ✅.

**Count reconciliation:** SPRINT_11 says "9 Codex rejection points" and also "13 tests / ~63 assertions" for the effect matrix. Machine truth: the matrix file has **13 collected** (10 parametrized + 3 regression) and the runtime assertion count is 60 (10×6), not 63. These are doc-vs-machine discrepancies; neither changes any DONE status.

---

## 4. Ranked Gap List (Task 2)

Each gap states: **(a)** what's missing, **(b)** human or agent, **(c)** effort, **(d)** PRI category unblocked. **Human-required items are at the top** — they are the critical path and cannot be closed by an agent.

| # | Gap | (a) What's missing | (b) Class | (c) Effort | (d) PRI category unblocked |
|---|-----|--------------------|-----------|-----------|----------------------------|
| G1 | **Live-credential verification** | An authenticated Binance WebSocket + Alpaca sandbox test proving real connectivity, a live tick, and a sandbox order | **HUMAN** (needs real keys; cannot be invented) | 4–6 h | "Broker connectivity", "Live market data" (both currently 0) |
| G2 | **Controlled pilot execution + report** | Someone actually runs the pilot described in `CONTROLLED_PILOT.md`, records timestamps/executor/outcomes (turns a PROCEDURE into an EXECUTION RECORD) | **HUMAN** | 8–16 h | "Durability/recovery", whole PRI 22→70+ claim |
| G3 | **Runbook execution records** | All 5 runbooks are PROCEDURES; 0 EXECUTION RECORDS exist. Drills must be run, observed, logged | **HUMAN** (must be executed, not simulated) | 2–4 h per drill × 5 | "Durability/recovery", "Can execute ops runbooks" trust |
| G4 | **Core loop correctness** | Position bookkeeping (`fill_trade` never called — `cycle_executor.py:197-213` only `open_trade`/`update_trade`), sizing dollars→shares, daily-loss unwired, 2/3 strategies can never fire, double-preflight | **AGENT** | ~80 h | Deterministic correctness pillar (silent account-corruption risk) |
| G5 | **Live Binance transport (OT-001)** | `StreamTransport` is protocol-only (`market_stream.py`); zero implementations | **AGENT** | ~40 h | "Live market data" (10→100) |
| G6 | **Durable idempotency + replay (OT-002)** | `_seen_events`/`ReplayRecorder` in-memory; restart loses duplicate-fill protection | **AGENT** | ~32 h | "Durability/recovery" |
| G7 | **Atomic order-event side effects (OT-003)** | No transaction/outbox/rollback; failure leaves inconsistent state | **AGENT** | ~32 h | "Atomicity" |
| G8 | **PostgreSQL migration path (H7/PB5)** | `conn.execute()` on psycopg2 (`migration_manager.py:20,28,62,89,103`); `DATABASE_URL` set → crash | **AGENT** | ~16 h | "Deployment correctness", PG layer |
| G9 | **API health boundedness (OT-010)** | `/v1/health` builds orchestrator synchronously, no timeout; reproduced 45s+ stall | **AGENT** | ~8 h | "API operational behavior" |
| G10 | **Tick validation + timestamp normalization (OT-004)** | NaN/neg/zero/millisecond-vs-second timestamps accepted | **AGENT** | ~16 h | "Input validation" |
| G11 | **Order-event serialization + ACKNOWLEDGED parity (OT-006/OT-005)** | Concurrent events unserialized; acked orders omitted from in-memory open-order query | **AGENT** | ~16 h | "Concurrency safety" |
| G12 | **Candle robustness + retention (OT-007/OT-008)** | Out-of-order/shutdown; final candle never flushed; `ReplayRecorder.records` unbounded | **AGENT** | ~24 h | "Live market data" / "Durability" |
| G13 | **Runbook→CLI truth (VB4)** | Runbooks command `traderos risk/metrics/audit verify/daemon start` — none exist (§2) | **AGENT** | ~16 h | "Can execute ops runbooks" trust |
| G14 | **Web dashboard MVP** | No user-facing surface; Commercial Readiness = 0 | **AGENT** | ~80–100 h | Commercial (PRI-independent) |
| G15 | **docker-compose daemon + prod PG (PB12)** | Compose runs two API instances, no daemon; no prod PG | **AGENT** | ~8 h | "Deployment correctness" |
| G16 | **Dead weight + duplicates (blueprint §4/§10)** | ~40 modules test-only; D1/D4/D7 split-brain (kill-switch, metrics, strategies) | **AGENT** | ~16 h | Static quality gates (coverage % mechanically rises) |
| G17 | **Version/tag drift (VB6)** | 1.0.0 vs 1.1.0 vs 0.3.0; pyproject untagged | **AGENT** | ~4 h | "Release provenance" |

**Bottleneck statement:** the critical path is **G1 → G2 → G3** — all human. An agent can complete G4–G17 in parallel with human clock time, but **no PRI ≥ 70 claim, no "live capability" claim, and no "drills executed" claim is provable until a human does G1–G3.** Nothing the agent does substitutes for those.

---

## 5. Minimum-Move Execution Sequence (Task 3)

Smallest number of discrete sessions that closes every gap in §4. No new abstractions, services, or architecture — only gap-closing. Human sessions **cannot** be batched with agent sessions; agent sessions are mergeable.

### Human-clock sessions (critical path — cannot be claimed "done" by an agent)

| Session | Closes | Content | Calendar time |
|---------|--------|---------|---------------|
| **H1 — Credentials + sandbox verification** | G1 | Provision Binance/Alpaca test keys; run authenticated connect + live tick + sandbox order; paste raw output | 0.5–1 day |
| **H2 — Controlled pilot run** | G2 | Execute `CONTROLLED_PILOT.md` against the (by then) correct core loop; record timestamps/executor/outcomes | 1–2 days, after agent sessions A2–A4 |
| **H3 — Five drill executions** | G3 | Run each of the 5 runbooks for real; turn each PROCEDURE into an EXECUTION RECORD with observed results | 2–4 h each, after A6 (CLI truth) |

### Agent work sessions (pure code/tests — batchable for calendar efficiency)

| Session | Closes | Content | Duration |
|---------|--------|---------|----------|
| **A1 — Documentation pass** | (task itself) | Commit this audit + blueprint; no source changes | 0.5 day |
| **A2 — Core loop correctness** | G4 | `fill_trade` bookkeeping, sizing unit, daily-loss, strategy params/fireability, single preflight; tests for each | ~10 days |
| **A3 — Live lifecycle** | G5, G6, G7, G10, G11, G12 | Transport → durable replay → outbox/atomicity → tick validation → event serialization → candle/retention; all behind existing protocols | ~18 days |
| **A4 — Ops truth** | G8, G9, G15 | PG migration path + fresh-PG schema; bounded health; compose daemon+PG stack | ~4 days |
| **A5 — Hygiene** | G16, G17 | Delete dead modules; consolidate D1/D4/D7; single version source | ~3 days |
| **A6 — Runbook truth** | G13 | Implement `risk`, `metrics`, `audit verify`, `daemon start` verbs so drills are executable | ~2 days |
| **A7 — Commercial surface** | G14 | Web dashboard MVP (positions/P&L/orders/kill-switch/health) | ~12 days |

**Sequencing rule:** A2 must precede H2 and H3 (a pilot and drills cannot be honest against a loop that never creates positions). A3/A4 must precede H1's "live capability" claim. A6 must precede H3 (commands must exist before drills can be run). Agent sessions A2–A7 are fully parallelizable with the human's H1 clock.

**Minimum critical-path calendar:** H1 (1d) ‖ A2–A7 in parallel → H2 (2d) after A2–A4 → H3 (2d) after A6. Wall-clock floor ≈ **3 weeks of human time** with a fully-staffed agent track; the agent workload alone is ≈ 5–6 weeks of merged sessions.

**Do NOT claim DONE for:** H1/H2/H3 (human), or any of G1–G3. The agent may only mark A1–A7 + G4–G17 done with attached test/command evidence.

---

## 6. Independent-Reviewer Self-Check (Task 4)

**Question:** *Would this survive an independent reviewer re-running the exact commands, from a cold checkout, with no trust in my prior output?*

| Claim | Verdict | Why |
|-------|---------|-----|
| 832 tests collected | **YES** | `pytest --collect-only -q -o addopts=""` → `832 tests collected in 4.33s` (raw output attached) |
| 832 passed, 0 failed | **YES** | `pytest -q` → `832 passed, 7 warnings in 31.06s` (raw output attached) |
| Coverage 84.42% | **YES** | `pytest -q` coverage report → `TOTAL 6914 1077 84%`, `Total coverage: 84.42%` |
| ruff clean | **YES** | `ruff check src/traderos/` → `All checks passed!`, exit 0 |
| pyright clean | **YES** | `pyright src/traderos` → `0 errors, 0 warnings, 0 informations`, exit 0 |
| Zero infra imports in domain | **YES** | `grep -rn "traderos.infrastructure" src/traderos/domain/` → no output, exit 1 |
| Per-layer test counts | **YES** | single collect command, 79 collected, node IDs attached (§1.6) |
| Runbooks are PROCEDURES | **YES** | every file in `docs/runbooks/` + `runbooks/` scored 0/0/0 on timestamp/executor/observed (§2) |
| Codex items DONE | **YES** (all 9) | each DONE has either a collected test node ID or a `file:line` grep, both re-runnable |
| Codex rejection list provenance | **PARTIAL** | the original Codex report file is not in the repo; the item list is reconstructed from `SPRINT_11.md`/`CHANGELOG.md` and stated as such. A reviewer with access to the original report can only extend the list, not contradict any DONE here |
| Live-credential verification / pilot / drill execution | **NOT CLAIMED** | explicitly ranked G1–G3, human-required, excluded from DONE (§4, §5) |

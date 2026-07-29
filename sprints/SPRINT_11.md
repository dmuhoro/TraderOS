# Sprint 11 — Programme Ω: Operational Verification Sprint

**Period:** 2026-07-29
**Objective:** Eliminate every remaining Codex rejection. This is NOT a feature sprint — this is an operational verification sprint. Verify audit integrity, broker reconciliation, live preflight, and operational recovery.

---

## GATE 1: Ω.1 — Audit Integrity ✅

| Item | Status | Evidence |
|------|--------|----------|
| `verify_chain()` recomputes every entry hash from field values | ✅ All 3 backends fixed | InMemory `audit.py:75-81`, SQLite `observability.py:92-108`, Postgres `observability_postgres.py:98-110` |
| Six-field mutation tests (action, actor, resource, detail, timestamp, previous_hash + hash) | ✅ 8 individual-field mutation tests | `test_audit_service.py`, `test_observability.py::TestSQLiteAuditService` |
| Multi-seed PYTHONHASHSEED verification | ✅ 5 seeds (0,1,42,12345,99999) | `test_audit_integrity.py::test_hash_is_independent_of_python_hash_seed` |
| SHA256 determinism + known-value test | ✅ | `test_audit_integrity.py` (5 tests) |
| ADR-008 updated to Accepted, matches implementation | ✅ | `docs/adr/ADR-008-audit-chain-sha256.md` |

## GATE 2: Ω.2 — Broker Reconciliation ✅

| Item | Status | Evidence |
|------|--------|----------|
| broker-only positions mismatch | ✅ | `test_broker_state_reconciliation.py::test_broker_only_position_detected` |
| local-only positions mismatch | ✅ | `test_local_only_position_detected` |
| quantity mismatch | ✅ | `test_quantity_mismatch_detected` |
| price mismatch | ✅ | `test_price_mismatch_detected` |
| broker-only orders mismatch | ✅ | `test_broker_only_order_detected` |
| local-only orders mismatch | ✅ | `test_local_only_order_detected` |
| stale snapshots | ✅ | Implemented in `_STALE_THRESHOLD_SECONDS=300` check |
| duplicate broker state | ✅ | `test_duplicate_broker_state_detected` |
| broker failures | ✅ | `test_reconciliation_fails_closed_on_broker_failure` |
| unknown state | ✅ | Generic mismatch classification |
| All mismatches fail reconciliation, trip KillSwitch, refuse orders | ✅ | `_handle_reconciliation_result` wires to KillSwitch (severity>=2), health (unhealthy), audit (record), metrics (counter) |
| All 10 mismatches proven via integration test | ✅ | `test_reconcile_with_all_10_mismatches_integration` |
| 14 reconciliation tests total | ✅ | All passing |

## GATE 2b: Ω.3 — Live Preflight ✅

| Item | Status | Evidence |
|------|--------|----------|
| PreflightService wired into production factory | ✅ | `factory.py` creates with audit+broker_recon+kill_switch |
| PreflightService passed to CycleExecutor | ✅ | `orchestrator.py::__post_init__`, `cycle_executor.py::__init__` |
| Preflight gate before broker order submission | ✅ | `cycle_executor.py:153-159` — preflight.check() before can_trade() |
| Preflight as pre_cycle_hook in DaemonController | ✅ | `orchestrator.py::_pre_cycle_check` |
| Spy test: preflight failure blocks broker call | ✅ | `test_preflight_execution_integration.py` (4 tests) |
| Spy test: blocked reconciliation blocks broker | ✅ | Same file |
| Spy test: engaged kill switch blocks broker | ✅ | Same file |
| Spy test: no preflight allows broker call | ✅ | Same file |

## PARALLEL TRACK: Ω.4 — Operational Recovery ✅

| Item | Status | Evidence |
|------|--------|----------|
| Timed backup test (< 5s SLO) | ✅ | `test_operational_recovery.py::TestTimedBackup` |
| Timed restore test (< 5s SLO) | ✅ | Same |
| Crash recovery drill (order reconciliation) | ✅ | `test_recovery_after_simulated_crash` |
| Kill-switch reset after recovery | ✅ | `test_kill_switch_resets_after_recovery` |
| Reconciliation recovers after broker outage | ✅ | `test_reconciliation_recovers_after_broker_outage` |
| Preflight passes after full recovery | ✅ | `test_preflight_passes_after_full_recovery` |
| Full reconciliation cycle drill | ✅ | `test_full_reconciliation_cycle` |
| Full reconciliation fix after mismatch | ✅ | `test_full_reconciliation_fix_after_mismatch` |
| recover_from_crash() accepts actual state | ✅ | `daemon_controller.py:128-136` |

## Programme C (carried forward) ✅

| Item | Status |
|------|--------|
| Rate-limit wrapper (flagged) | ✅ `infrastructure/broker_rate_limiter.py` |
| Operations runbook | ✅ `docs/runbooks/OPERATIONS.md` |
| Controlled-pilot params | ✅ `docs/runbooks/CONTROLLED_PILOT.md` |
| Cold incident drill | ✅ `docs/runbooks/COLD_INCIDENT_DRILL.md` |
| Deployment rollback drill | ✅ `docs/runbooks/DEPLOYMENT_ROLLBACK_DRILL.md` |

---

## Key Files Created/Modified

### Modified files
| File | Change |
|------|--------|
| `src/traderos/infrastructure/audit.py` | `verify_chain()` now recomputes hash from fields |
| `src/traderos/infrastructure/observability.py` | SQLite `verify_chain()` recomputes hash, checks content integrity |
| `src/traderos/infrastructure/observability_postgres.py` | Postgres `verify_chain()` recomputes hash, uses named column lookup |
| `src/traderos/domain/services/broker_state_reconciliation_service.py` | Full 10-mismatch detection engine with MismatchType enum, local state comparison, `reconcile()` accepts local positions/orders |
| `src/traderos/application/daemon_controller.py` | Reconciliation wired to KillSwitch/audit/metrics per mismatch type. `recover_from_crash()` accepts actual state params. `_handle_reconciliation_result` records audit entries + metric counters |
| `src/traderos/application/cycle_executor.py` | Accepts `preflight_service`, calls `check()` before order submission |
| `src/traderos/application/orchestrator.py` | Accepts `preflight_service`, wires as pre_cycle_hook + cycle_executor dependency |
| `src/traderos/application/factory.py` | Creates `PreflightService` with audit+broker_recon+kill_switch, passes to orchestrator |
| `docs/adr/ADR-008-audit-chain-sha256.md` | Updated to Accepted status, verify_chain() behavior documented accurately |

### New test files
| File | Tests |
|------|-------|
| `tests/test_audit_integrity.py` | 5 tests: SHA256 determinism, known value, distinct inputs, canonical JSON excludes hash, multi-seed PYTHONHASHSEED |
| `tests/test_preflight_execution_integration.py` | 4 tests: spy/mock proving broker.send blocked when preflight fails |
| `tests/test_operational_recovery.py` | 8 tests: timed backup/restore, crash recovery drills, reconciliation drills |

### New runbook files (Programme C)
| File | Description |
|------|-------------|
| `docs/runbooks/OPERATIONS.md` | Operations runbook |
| `docs/runbooks/CONTROLLED_PILOT.md` | Controlled-pilot parameters |
| `docs/runbooks/COLD_INCIDENT_DRILL.md` | Cold incident drill |
| `docs/runbooks/DEPLOYMENT_ROLLBACK_DRILL.md` | Deployment rollback drill |
| `src/traderos/infrastructure/broker_rate_limiter.py` | Rate-limited broker adapter |
| `tests/test_broker_rate_limiter.py` | Rate-limit wrapper tests |

## Test Summary

| Metric | Value |
|--------|-------|
| Total tests | 801 passing (1 pre-existing failure in uncommitted sprint9 code) |
| New tests added (Ω programme) | 34 |
| Regressions | 0 |
| Coverage threshold | 70% (MEP §17 interim) |

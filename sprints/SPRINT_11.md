# Sprint 11 — Track 2 / Programme C: Operational Readiness & Controlled Pilot

**Period:** 2026-07-29
**Objective:** Deliver remaining Operational Readiness items: rate-limit wrapper for broker adapter, operations runbook, controlled-pilot parameters doc, cold incident drill, and deployment rollback drill.

---

## Programme C — Operational Readiness ✅

| Item | Status |
|------|--------|
| Rate-limit wrapper (`infrastructure/broker_rate_limiter.py`) | Flagged `BrokerAdapter` proxy — disabled by default (`BROKER_RATE_LIMIT_ENABLED`), wraps all 6 broker methods with configurable per-method rate limits |
| Rate-limit tests | 6 tests: disabled by default, enabled blocks, separate method buckets, env-var config, all methods wrapped, disabled via false |
| Operational runbook (`docs/runbooks/OPERATIONS.md`) | Backup/restore procedures, incident response lifecycle (SEV-1/2/3), kill-switch activation, recovery, monitoring metrics, health endpoints, logging |
| Controlled-pilot params (`docs/runbooks/CONTROLLED_PILOT.md`) | Preflight gate checks, risk parameters (MAX_POSITION_SIZE, drawdown, daily loss), rate-limit config, reconciliation requirements, exit criteria |
| Cold incident drill (`docs/runbooks/COLD_INCIDENT_DRILL.md`) | 4-phase drill (teardown → restore → startup → verification) with 35-minute SLO |
| Deployment rollback drill (`docs/runbooks/DEPLOYMENT_ROLLBACK_DRILL.md`) | 4-phase rollback (contain → code rollback → verify → resume) with 15-minute SEV-1 SLO |

---

## Key Files Created/Modified

### New files
- `src/traderos/infrastructure/broker_rate_limiter.py` — Rate-limited `BrokerAdapter` proxy
- `tests/test_broker_rate_limiter.py` — 6 tests for rate-limit wrapper
- `docs/runbooks/OPERATIONS.md` — Operations runbook
- `docs/runbooks/CONTROLLED_PILOT.md` — Controlled-pilot parameters
- `docs/runbooks/COLD_INCIDENT_DRILL.md` — Cold incident drill
- `docs/runbooks/DEPLOYMENT_ROLLBACK_DRILL.md` — Deployment rollback drill

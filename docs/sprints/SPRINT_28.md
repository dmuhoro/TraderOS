# Sprint 28 — Product track: user accounts + per-user risk rails + manufacturing meta

**Period:** 2026-08-07
**Objective:** Implement Track B's pilot-feeding rails and Track M's manufacturing
meta, per `PILOT_TO_PRODUCT.md` and `BUILD_PRINCIPLES.md`. Deliver the
user/account model (B1), then the per-user risk rails with `user_id` audit
attribution enforced **at the real live submission boundary** (B2), and forge
the FounderOS manufacturing loop on TraderOS itself (M1–M4).

**Reference docs:** `docs/engineering/PILOT_TO_PRODUCT.md`,
`docs/engineering/BUILD_PRINCIPLES.md`, `docs/engineering/FOUNDEROS_WORKFLOW_SPEC.md`,
`docs/evidence/2026-08-07_user_account_drill.log`.

---

## Ground truth (verified, not assumed)
- Live chain (`application/factory.py`): `CycleExecutor → JournaledBroker →
  GuardrailedBroker → RateLimitedBroker → AlpacaBrokerAdapter`. Order call
  sites in `cycle_executor.py` (`can_trade` + `authorize_order`).
- Per-user rails are enforced **at those two call sites** by threading
  `trading_user_id` from factory → orchestrator → executor, and each per-user
  profile is resolved from config (`risk.per_users`) by a `PerUserRiskResolver`
  installed on the `RiskService`.
- Unknown user + resolver configured → **denied fail-closed** (no silent open).
  A per-user `engaged` flag is an operator-scoped kill switch that halts that
  trader alone, never others, never the global path.

## Work Completed

### B1 — User/account model (committed earlier this sprint)
- `domain/entities/user.py`: `User`, `UserSession`, `UserApiKey` + roles/statuses.
- `domain/repositories/user_repository.py` port + SQLite impl
  (`infrastructure/repositories/sqlite/users.py`).
- `domain/services/account_service.py`: salted PBKDF2-HMAC-SHA256 + constant-time
  compare, expiring sessions (denied + evicted), one-time per-user API keys
  (only SHA-256 persisted, revoked keys deny), admin bootstrap from
  `TRADEROS_ADMIN_USERNAME`/`PASSWORD`.
- migration `v008_user_accounts.py` (schema version 8, SQLite + PG).
- Proof: `tests/test_account_service.py`, account drill
  (`docs/evidence/2026-08-07_user_account_drill.log`), integration in
  `tests/integration/test_factory.py`.

### B2 — Per-user risk rails + `user_id` attribution (this change)
- `PerUserRiskProfile` (user-scoped: gross exposure, position size, position
  count, daily loss pct, allowlist, plus a fail-closed `engaged` operator kill
  switch). All defaults bounded — no unlimited allowance.
- `PerUserRiskResolver`: unknown users fail closed (denied, not silently allowed).
- Enforcement at the real boundary — `cycle_executor.py` `can_trade` +
  `authorize_order` now take `user_id=`; `Orchestrator` threads
  `trading_user_id`; `factory.py` builds a resolver from `risk.per_users` and
  sets `trading_user_id` from `risk.operator_user_id`.
- Scoped kill switch: an engaged profile blocks only that trader; other traders
  and the global system path are unaffected.
- Proof:
  - `tests/test_per_user_rails.py` (attribution, caps, allowlist, fail-closed,
    scoped kill-switch).
  - `tests/test_cycle_risk_gate.py` **boundary proof**: an engaged per-user
    profile through the real `CycleExecutor` leaves `broker.place_market_order`
    NEVER called (`result.trades == 0`).
  - `tests/test_factory_ingestion.py`: config → resolver wiring on the real
    `build_orchestrator` path.

### Track M — manufacturing meta (FounderOS, bootstrapped on TraderOS)
- **M1** already present: `docs/engineering/BUILD_PRINCIPLES.md` (7 principles +
  5-step loop + instantiation recipe).
- **M2** `docs/engineering/FOUNDEROS_WORKFLOW_SPEC.md`: the one-page task
  template — scope / exit test / blast radius / reviewer / evidence path, with
  the define→gate→execute→verify→lock loop.
- **M3** wired into `.ai/context/13_playbook.md`: the five-field task template
  is mandatory before labour; specialized-agent roles exist under
  `.ai/agents/`.
- **M4** blast-radius tiering in the playbook: execution/risk paths
  human-gated (fail-closed real-submission proof required); CRUD/copy paths
  lightweight-gated. Default is Tier 1 when unknown.

## Gates (delta on this change)
- Full suite baseline before this change: 5 failures (3 stale v008 migration
  assertions, 1 perf band, 1 PG env). After: 0 deterministic failures.
- Lint (`ruff check`): clean on all touched files.
- Typecheck (`pyright`): 0 errors on touched files.

## Not in scope / still open (honest)
- **B3 retail-facing UI** and **B4 attribution/regulator UI** remain open —
  deliberately post-pilot product track, not a pilot gate (per
  `PILOT_TO_PRODUCT.md`). The operator dashboard base exists
  (`interfaces/api/dashboard/`).
- Full-suite runs may flash 2 environment-ordered flakes — a real-Postgres
  table name collision in `test_migration_v004` and a subprocess
  drill SIGTERM under full-suite load (`test_ha_failover`). Both pass
  deterministically in isolation and are unrelated to this change.
- Real Alpaca/Binance credentialed tests remain NO-GO without credentials.

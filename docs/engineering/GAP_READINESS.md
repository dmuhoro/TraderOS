# TraderOS — Gap-Readiness Checklist & Build Order

**Generated:** 2026-08-09 · **Branch:** `main` · **HEAD:** `SPRINT_31` (pending push)
**Method:** every item below is scored from *measured evidence in this repo*
(test suite, drills in `docs/evidence/`, committed architecture) plus the
verified live submission path. A score is not a promise — the "Exit test" is
the only thing that moves it. This document exists to make the **GO/NO-GO on
real capital** an auditable decision rather than a vibe.

**Scoring legend**
- **Score** = current readiness 0–100.
- **Risk** = cost of shipping real capital with this gap unfixed
  (CRITICAL / HIGH / MEDIUM / LOW).
- **Exit test** = the observable, reproducible condition that marks it DONE.

---

## Gap register

| ID | Gap | Score | Risk | Current evidence | What's missing | Exit test |
|----|-----|:---:|:---:|---|---|---|
| G-01 | Backtest realism | 80 | HIGH | Real Alpaca+Binance data, durable store, engine fills+metrics; **cost model now includes fee + slippage + `latency_bps`**; keyless **cost-adjusted walk-forward evidence on a frozen oracle dataset** with a withheld 35% out-of-sample window (5 folds, full costs) (`sprint27_walk_forward_evidence.log`) | **No strategy shows positive expectancy after full costs on OOS data** — honest callout: pilot is **DATA-VALIDATION ONLY**, no PnL claim (per `LIVE_RUN_POLICY.md`); real-market latency model still to be tuned from live fills | A strategy that shows positive expectancy *after* full costs on out-of-sample real data (withheld window) — mechanics proven; edge not yet demonstrated |
| G-02 | Live order ops: ack/timeout, partial fills, reconnect, broker↔journal reconcile | 80 | CRITICAL | Durable journal, restart + Binance + Postgres drills, per-order risk gate, caller-owned `client_order_id` threaded end-to-end (dedupe on restart, intent-idempotency), 300-cycle forced-disconnect soak, plus **partial-fill/reconnect drill** (50% fills + ack drops through the real path: 0 dup/lost, restart re-submits nothing — `sprint27_partial_fill_reconnect.log`), **real-paper soak harness that fails closed without credentials**, and a **real Alpaca paper smoke-soak that now PASSES** on the real paper endpoint (`2026-08-09_final_smoke.log`, 3 cycles; `_smoke5.log`, `_smoke3.log` 5 cycles) with WP6 submit→ack latency across the real-paper runs (min 269–306 ms, median 307–308 ms, max 308–356 ms) | The soak run exposed and fixed a real production defect (Alpaca string `qty`/`filled_qty`, unconditional `filled=True` — see below); a **continuous 24–72h unattended window** is the operator-run gate (`run_unattended_paper_soak.py`, 5/5 PASS on a 60s supervision test); WS resync vs live API untested | Unattended **paper-broker soak (Alpaca paper, 24–72h)**: 0 reconcile errors, 0 duplicate/lost orders across forced disconnects, journal-recovery replays correctly — **bounded real-paper runs passing; full window pending operator time** |
| G-03 | Portfolio-level risk rails | 80 | HIGH | Per-order notional + daily-loss gate (2%-of-equity fail-closed) at submission seam, keyed to `client_order_id`; **risk-rails drill 6/6 fail-closed** through the real loop: gross-exposure cap blocks, allowlist blocks unlisted + passes allowlisted to broker, kill-switch flatten exactly-once, data-gap blocks live (`sprint27_risk_rails_drill.log`) | Symbol/notional allowlists and caps proven via drill but defaults for production config still to be set; kill-switch is a scripted call, no hotkey/on-call surface yet | A drill where a portfolio cap and a kill-switch-flatten each provably stop the live loop; allowlist blocks an unlisted symbol — **proven**, remaining work is production config + operational surface |
| G-04 | Firm ops: HA/supervision, alerting, key management, release signing | 80 | HIGH | Docker/compose/Railway/CI; audit trail; supervision wiring with unclean-shutdown detection + CRITICAL alert on forced kill; **lease-based HA failover** (`FailoverManager`/`ha_failover.py`, stale-after-90s takeover, fail-closed standby) + **secrets rotation with value-redacted access audit**; **real HashiCorp Vault KV-v2 secret-manager integration wired into the LIVE boot path** (`VaultSecretProvider`, resolved through the same `_build_secret_rotator` the live factory calls; value-redacted access audit; fail-closed when absent — `vault_secret_manager_drill.log` 5/5 + `sprint27_firm_ops_drill.log`); **on-call transport drill 6/6** (`oncall_transport_drill.log`); **WP10 PagerDuty events/v2 + Slack webhook provider transports now wired into the live on-call router** (env-gated on `PAGERDUTY_ROUTING_KEY` / `SLACK_WEBHOOK_URL`, fail-closed construction without keys, delivered + ack-verified on the real HTTP wire in `test_oncall_providers.py`) | Cloud KMS rotation cadence not yet exercised against a managed Vault/KMS instance (local dev Vault proven); the on-call providers are wired but no managed on-call platform account with live keys has yet received a real incident; **operator login is now a PG/HMAC-backed session with username+password (WP8), never a static roaming API key** | Alert delivered on a forced process kill — **proven**; HA failover proven — **proven**; live keys in a secret manager with rotation + access audit — **proven against a real Vault**; on-call provider fan-out proven on the wire — **delivery to a live PagerDuty/Slack account pending operator keys**; remaining work is a managed instance + managed on-call platform with credentials |
| G-05 | Causal trade accountability | 80 | MEDIUM | Audit log (`risk.*`, `trade.executed`); `CycleExecutor` records signal → decision → order → fill causal chain (signal_id-keyed); replay service reconstructs per-strategy FIFO realized PnL; **multi-restart replay drill**: 9 real-path cycles, 2 simulated process restarts on the same durable DB, audit chain valid + every cycle reconstructed bit-complete (`sprint27_multirestart_replay.log`) | Attribution not yet surfaced to a UI/regulator view | Replay a full trading day and reconstruct *why* each fill happened, bit-identical to the recorded events — **proven across restarts**; UI surfacing still open |
| G-06 | Test realism / oracle | 80 | MEDIUM | 1351 tests, 92%+ coverage, conformance + dependency-direction tests, forced-disconnect + supervision + secret-hygiene + causal-replay + governance drills, plus **oracle conformance drill**: engine reproduces committed reference PnL on the frozen dataset AND the withheld window to tolerance 1e-4 (`sprint27_oracle_conformance.log`) | Reference PnL oracle only covers the frozen G-06 candles, not every strategy; no CI job for the evidence drills (run locally) | A known strategy run through a frozen dataset reproduces a committed reference PnL to a stated tolerance — **proven 2/2** |
| G-07 | Governance for real capital | 85 | MEDIUM | Constitution, ADRs, release constitution, sprint docs, `LIVE_RUN_POLICY.md` (red-lines, kill authority, env separation, credential policy, pilot terms), HMAC release signing + fail-closed live gate, **operator acknowledgment recorded via HMAC-signed `operator_ack.py` + `verify_ack` check wired into the live gate**, **governance job in CI** (paper pass-through, live posture must fail), **governance drill 6/6** (`sprint27_governance_drill.log`); live keys now resolve through the real secret manager on the LIVE path (`vault_secret_manager_drill.log`) | live gate in CI asserts fail-closed live posture but real keys are never in CI | A documented live-run policy (red-lines, kill authority, release signing) reviewed and committed before any real capital, with the operator's written acknowledgment — **proven**; secret-manager integration — **proven** |

---

## Build order (risk-rated sequence)

| Order | Work | Risk | Why here | Size |
|---|---|---|---|---|
| 1 | **G-02 paper-broker soak** (real Alpaca paper, unattended) | CRITICAL | The GO/NO-GO milestone — proves placement→fill→reconcile→recover under real broker behavior. **Bounded real-paper runs PASS** on the real paper endpoint; the soak exposed + fixed Alpaca string-quantity/honest-fill defects. Remaining: the continuous 24–72h window (operator-run). | bounded runs done; full window open |
| 2 | **G-01 backtest realism** (cost model + walk-forward) | HIGH | Mechanics proven (fee/slippage/latency + withheld OOS). Remaining: a real edge proof or an honest **DATA-VALIDATION-ONLY** pilot. | mechanics done; edge open |
| 3 | **G-03 portfolio risk rails** (caps, kill-flatten, allowlists) | HIGH | Drill proves the rails fail closed against the real loop. Remaining: production config defaults + operational kill surface. | drill done; config open |
| 4 | **G-04 alerting + HA + keys** | HIGH | Alert + HA proven by drills. Remaining: secret-manager integration + on-call transport. | drills done; integration done (Vault drill 5/5, on-call 6/6) — managed instance open |
| 5 | **G-06 oracle/conformance** | MEDIUM | Engine reproduces committed reference PnL 2/2 (full + withheld). Done as far as the frozen dataset covers. | done |
| 6 | **G-05 causal replay** | MEDIUM | Replay proven bit-complete across 2 restarts. Remaining: UI/regulator surfacing. | replay done; operator-surfacing open (regulator view still open) |
| 7 | **G-07 governance red-lines + release signing** | MEDIUM | Operator acknowledgment + live gate in CI + drill 6/6. Remaining: secret-manager integration. | done; secret-manager integration done (Vault drill) |

**Critical path to GO/NO-GO:** G-02 → G-01 → G-03/G-04 → controlled, human-supervised real pilot.

---

## GO / NO-GO definition (the only gate that matters)

**GO requires all of the following, empirically demonstrated — not declared:**

1. G-02 paper soak **passes** (see exit test above).
2. G-03 kill-switch-flatten + portfolio cap drill **pass** against the real loop.
3. G-01 cost-adjusted walk-forward shows a genuine edge (or the pilot is capped to "data validation only", no PnL claim).
4. G-04 alert + HA failover proven; live keys in a secret manager.
5. A documented live-run policy (G-07) is committed and the operator acknowledges red-lines in writing.
6. **Pilot terms:** real capital is deployed in a small, human-supervised, explicitly-bounded pilot (symbol + notional caps), with a hard stop condition defined *before* launch.

**NO-GO until then is the default, not a failure state.**

---

## Honesty notes

- Closing one gap does **not** close any other. A green drill is a *measured
  precondition* for the next, not a guarantee.
- Sprint 27 moved G-01 through G-07 to 80+, but every exit test that depends on
  the **real broker** (G-02 Alpaca paper soak) or **real markets** (G-01 edge
  proof) is still open and stays open until it runs with live credentials.
- **G-01 is the honest callout:** after full costs (fee 10bps + slippage 5bps +
  latency 10bps) on the withheld out-of-sample window, *no* strategy shows
  positive expectancy. That means the pilot's terms are **DATA-VALIDATION
  ONLY** — the software is proven to compute cost-adjusted PnL, but no PnL
  claim can be made yet. This is recorded, not papered over.
- **G-02's real-paper soak harness is ready and fails closed** (exit code 2,
  NO-GO) when Alpaca paper keys are absent. Bounded runs against the real
  Alpaca paper endpoint now **PASS** (0 lost intents, clean reconcile, own
  residue closed out) and exposed + fixed two real production defects on the
  live path: Alpaca returns `qty`/`filled_qty` as strings (the adapter did
  string arithmetic) and `filled=True`/`status=filled` was reported
  unconditionally even for pending orders. A continuous **24–72h unattended
  window** via `run_unattended_paper_soak.py` (5/5 PASS on a 60s supervision
  test) remains the operator-run gate; until it runs for the full window, the
  exit test is **not** claimed as met.
- G-04 alerting is proven in-process (supervision + firm-ops drills); HA
  failover is proven via lease semantics + takeover drill. Secret-manager
  integration is now proven against a **real HashiCorp Vault** (KV-v2) on the
  LIVE boot path, with value-redacted access audit and fail-closed fallback
  (`vault_secret_manager_drill.log` 5/5). Remaining: exercise rotation cadence
  against a managed Vault/KMS instance.
- **Operational state is now surfaced to the operator dashboard from real
  services** (not duplicated snapshots): `/v1/orchestrator/status` carries the
  HA lease/leader state (read from the durable lease file), the on-call
  delivered/failed counters the router itself writes, the secret-rotator
  version map, and the configured `trading_user_id`; `trading_user_id` is also
  threaded into `/v1/positions`, `/v1/orders` and `/v1/trades` at the response
  seam. Source-truth proven by `operational_health_drill.log` 6/6 (the panel's
  on-call count moves exactly with real kill-switch trips on the wire).
  Unconfigured subsystems are reported as such — never claimed as protected.
- The live gate now runs in CI (`governance` job): paper posture passes
  through, live posture is asserted to **fail** (fail-closed) with no GO
  conditions. The gate itself is not a bypass — it enforces the red-lines.
- **WP8 — operator login is session-based, not key-based.** The dashboard no
  longer persists a static roaming API key; sign-in is username+password
  against `/v1/auth/login`, the server mints a short-lived PG/HMAC-backed
  session token held only in the closing page session, and `/v1/auth/logout`
  revokes it, with `login`/`login_denied` on the audit trail. Session tokens
  are an alternative credential seam (`X-Session-Token`), RBAC-gated exactly
  like API keys — a viewer session can read but never operate or trip the
  kill switch (proven in `test_operator_login.py`). This narrows the
  localStorage-XSS surface; it is not a panacea for XSS.
- **WP9 — Market Overview + Research Lab** are served from the real runtime
  services (DataIngestionService + AnalysisService + the strategy registry +
  ResearchService), not duplicate snapshots: `/v1/market/overview`,
  `/v1/market/candles`, `/v1/market/symbols`, `/v1/research/indicators`,
  `/v1/research/backtest` (a registered strategy against that symbol's real
  candles), and `/v1/research/observations`. Indicator values in the response
  are asserted equal to `AnalysisService` output in tests; unknown symbols
  fail 404, never a silent empty claim.
- These scores supersede the older 96%/PRI-74 dashboards where they conflict:
  PRI-style indices measure architecture+process completion; this register
  measures *what must be true before real capital moves*.

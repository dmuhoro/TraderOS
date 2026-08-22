# TraderOS — Gap-Readiness Checklist & Build Order

**Generated:** 2026-08-13 · **Branch:** `main` · **HEAD:** `48f28b0` (Sprint 36)
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
| G-01 | Backtest realism | 85 | HIGH | Real Alpaca+Binance data, durable store, engine fills+metrics; **cost model now includes fee + slippage + `latency_bps`**; keyless **cost-adjusted walk-forward evidence on a frozen oracle dataset** with a withheld 35% out-of-sample window (5 folds, full costs) (`sprint27_walk_forward_evidence.log`); **`POST /v1/backtest` now runs on the real ingested candle series and fails closed on unknown/empty symbols** (Sprint 41) — no synthetic in-place results | **No strategy shows positive expectancy after full costs on OOS data** — honest callout: pilot is **DATA-VALIDATION ONLY**, no PnL claim (per `LIVE_RUN_POLICY.md`); real-market latency model still to be tuned from live fills | A strategy that shows positive expectancy *after* full costs on out-of-sample real data (withheld window) — mechanics proven; edge not yet demonstrated |
| G-02 | Live order ops: ack/timeout, partial fills, reconnect, broker↔journal reconcile | 85 | CRITICAL | Durable journal, restart + Binance + Postgres drills, per-order risk gate, caller-owned `client_order_id` threaded end-to-end (dedupe on restart, intent-idempotency), 300-cycle forced-disconnect soak, plus **partial-fill/reconnect drill** (50% fills + ack drops through the real path: 0 dup/lost, restart re-submits nothing — `sprint27_partial_fill_reconnect.log`), **real-paper soak harness that fails closed without credentials**, and a **real Alpaca paper smoke-soak that now PASSES** on the real paper endpoint (`2026-08-09_final_smoke.log`, 3 cycles; `_smoke5.log`, `_smoke3.log` 5 cycles) with WP6 submit→ack latency across the real-paper runs (min 269–306 ms, median 307–308 ms, max 308–356 ms) | A **continuous 24–72h unattended window** is the operator-run gate (`run_unattended_paper_soak.py`, 5/5 PASS on a 60s supervision test) | Unattended **paper-broker soak (Alpaca paper, 24–72h)**: 0 reconcile errors, 0 duplicate/lost orders across forced disconnects, journal-recovery replays correctly — **RUNNING in the cloud since 2026-08-22T07:56Z** (dedicated `traderos-soak` Railway service, self-supervised hourly batches ×10 cycles through the real chain; batch 001 PASS, submit→ack median 75 ms; window ends ~2026-08-25T07:56Z, final PASS requires every batch green) — *Sprint 44: operator keys landed, soak launched (`2026-08-22_operator_gates_soak_launch.log`); Sprint 45: WS-resync reconciliation vs live Binance API proven live — 3 forced outages, 2 resyncs, gap+divergence healed by mop-up, gapless + kline-identical convergence, VERDICT PASS (`2026-08-22_ws_resync_drill.log`)* |
| G-03 | Portfolio-level risk rails | 85 | HIGH | Per-order notional + daily-loss gate (2%-of-equity fail-closed) at submission seam, keyed to `client_order_id`; **risk-rails drill 6/6 fail-closed** through the real loop: gross-exposure cap blocks, allowlist blocks unlisted + passes allowlisted to broker, kill-switch flatten exactly-once, data-gap blocks live (`sprint27_risk_rails_drill.log`); **WP11 production config now armed and enforced at boot** — `risk_config.resolve_risk_rails` range-checks every rail, LIVE refuses to arm without explicit daily-loss/gross-exposure/position-size/max-positions + allowlist, and the live gate runs the same validator as check #5; **WP11b kill surface now audited + metered + deliberate** — engage/disengage write `risk.kill_switch_*` to the durable audit trail + metrics counters, dashboard requires explicit confirmation; **rate-limiter load-shedding proven under sustained burst** (`2026-08-22_rate_limiter_burst_drill.log`, 13/13): rejections are explicit, contained by the cycle/daemon (no crash), do not trip the broker circuit breaker, and HTTP 429s carry `Retry-After` + `X-RateLimit-*` with traffic resuming after the window | An operator must still populate the LIVE `allowed_markets` with the pilot symbols at deployment; live config applies to the paper defaults only until G-02 opens the real path | A drill where a portfolio cap and a kill-switch-flatten each provably stop the live loop; allowlist blocks an unlisted symbol — **proven**; production config + operational kill surface — **now closed (WP11/WP11b)** |
| G-04 | Firm ops: HA/supervision, alerting, key management, release signing | 80 | HIGH | Docker/compose/Railway/CI; audit trail; supervision wiring with unclean-shutdown detection + CRITICAL alert on forced kill; **lease-based HA failover** (`FailoverManager`/`ha_failover.py`, stale-after-90s takeover, fail-closed standby) + **secrets rotation with value-redacted access audit**; **real HashiCorp Vault KV-v2 secret-manager integration wired into the LIVE boot path** (`VaultSecretProvider`, resolved through the same `_build_secret_rotator` the live factory calls; value-redacted access audit; fail-closed when absent — `vault_secret_manager_drill.log` 5/5 + `sprint27_firm_ops_drill.log`); **on-call transport drill 6/6** (`oncall_transport_drill.log`); **WP10 PagerDuty events/v2 + Slack webhook provider transports now wired into the live on-call router** (env-gated on `PAGERDUTY_ROUTING_KEY` / `SLACK_WEBHOOK_URL`, fail-closed construction without keys, delivered + ack-verified on the real HTTP wire in `test_oncall_providers.py`) | Cloud KMS rotation cadence not yet exercised against a managed Vault/KMS instance (local dev Vault proven); the on-call providers are wired but no managed on-call platform account with live keys has yet received a real incident; **operator login is now a PG/HMAC-backed session with username+password (WP8), never a static roaming API key** | Alert delivered on a forced process kill — **proven**; HA failover proven — **proven**; live keys in a secret manager with rotation + access audit — **proven against a real Vault**; on-call provider fan-out proven on the wire — **delivery to a live PagerDuty/Slack account pending operator keys**; remaining work is a managed instance + managed on-call platform with credentials |
| G-05 | Causal trade accountability | 85 | MEDIUM | Audit log (`risk.*`, `trade.executed`); `CycleExecutor` records signal → decision → order → fill causal chain (signal_id-keyed); replay service reconstructs per-strategy FIFO realized PnL; **multi-restart replay drill**: 9 real-path cycles, 2 simulated process restarts on the same durable DB, audit chain valid + every cycle reconstructed bit-complete (`sprint27_multirestart_replay.log`); **WP12 surfaces it as a read-only regulator view** on the operator dashboard (`/v1/attribution/replay` window rendering the causal chain + per-fill realized PnL) | None within the software scope — the panel is read-only; an operator must still review it as part of the G-07 governance loop | Replay a full trading day and reconstruct *why* each fill happened, bit-identical to the recorded events — **proven across restarts**; UI surfacing — **now closed (WP12)** |
| G-06 | Test realism / oracle | 90 | MEDIUM | **2139 tests, 100% line coverage (0 missing of 12,139 statements, gate raised to `fail_under = 100`)**, conformance + dependency-direction tests, forced-disconnect + supervision + secret-hygiene + causal-replay + governance drills, plus **oracle conformance drill**: engine reproduces committed reference PnL on the frozen dataset AND the withheld window to tolerance 1e-4 (`sprint27_oracle_conformance.log`); **WP13 runs all 18 credential-free drills as a CI job** (`evidence-drills` in `.github/workflows/ci.yml`) — the build fails if any drill regresses; 7 credential/network/instance-gated drills are asserted out of the deterministic drill job (credential-gated ones stay operator-run; network-gated ones are exercised by the test suite when reachable). The real-market walk-forward is part of the deterministic set because it reuses the committed frozen dataset network-free (SPRINT_40). | Reference PnL oracle only covers the frozen G-06 candles, not every strategy; the CI drill job covers credential-free drills only (credential-gated ones stay operator-run); 100% is measured over the suite **as-run** — PG-gated tests still skip when Postgres is unreachable | A known strategy run through a frozen dataset reproduces a committed reference PnL to a stated tolerance — **proven 2/2**; evidence drills now enforced in CI — **closed (WP13)**; 100% coverage with a 100 fail-under gate — **closed (Sprint 35)** |
| G-07 | Governance for real capital | 85 | MEDIUM | Constitution, ADRs, release constitution, sprint docs, `LIVE_RUN_POLICY.md` (red-lines, kill authority, env separation, credential policy, pilot terms), HMAC release signing + fail-closed live gate, **operator acknowledgment recorded via HMAC-signed `operator_ack.py` + `verify_ack` check wired into the live gate**, **governance job in CI** (paper pass-through, live posture must fail), **governance drill 6/6** (`sprint27_governance_drill.log`); live keys now resolve through the real secret manager on the LIVE path (`vault_secret_manager_drill.log`) | live gate in CI asserts fail-closed live posture but real keys are never in CI | A documented live-run policy (red-lines, kill authority, release signing) reviewed and committed before any real capital, with the operator's written acknowledgment — **proven**; secret-manager integration — **proven** |

---

## Build order (risk-rated sequence)

| Order | Work | Risk | Why here | Size |
|---|---|---|---|---|
| 1 | **G-02 paper-broker soak** (real Alpaca paper, unattended) | CRITICAL | The GO/NO-GO milestone — proves placement→fill→reconcile→recover under real broker behavior. **Bounded real-paper runs PASS** on the real paper endpoint; the soak exposed + fixed Alpaca string-quantity/honest-fill defects. Remaining: the continuous 24–72h window (operator-run). | bounded runs done; full window open |
| 2 | **G-01 backtest realism** (cost model + walk-forward) | HIGH | Mechanics proven (fee/slippage/latency + withheld OOS). Remaining: a real edge proof or an honest **DATA-VALIDATION-ONLY** pilot. | mechanics done; edge open |
| 3 | **G-03 portfolio risk rails** (caps, kill-flatten, allowlists) | HIGH | Drill proves the rails fail closed against the real loop. **WP11 production config now armed + enforced at boot; WP11b kill surface audited/metered/confirmed.** Remaining: operator fills LIVE `allowed_markets` at deployment. | drill + config + kill surface done; live symbol set is an operator deployment step |
| 4 | **G-04 alerting + HA + keys** | HIGH | Alert + HA proven by drills. Remaining: secret-manager integration + on-call transport. | drills done; integration done (Vault drill 5/5, on-call 6/6) — managed instance open |
| 5 | **G-06 oracle/conformance** | MEDIUM | Engine reproduces committed reference PnL 2/2 (full + withheld). **WP13 runs the credential-free drills in CI.** | done; CI-enforced (WP13) |
| 6 | **G-05 causal replay** | MEDIUM | Replay proven bit-complete across 2 restarts. **WP12 surfaces the regulator attribution view.** | replay + regulator view done |
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
- **WP11 — production risk rails are now configured AND enforced at boot.**
  `risk_config.resolve_risk_rails` range-checks every rail (invalid values
  raise, never coerce), env overrides (`RISK_*`) win over yaml, and LIVE
  refuses to arm unless daily-loss/gross-exposure/position-size/max-positions
  are explicitly set with `require_allowlist=true` + a non-empty
  `allowed_markets`. The live gate runs the same validator as check #5. The
  resolved rails arm the real `authorize_order` gate on the live submission
  seam (`factory.py:190-202` over `cycle_executor.py:343/521`). Enforcing the
  config does not remove the operator's duty to set the actual pilot symbols at
  deployment.
- **WP11b — the kill switch is a deliberate, audited, metered surface.**
  engage/disengage write `risk.kill_switch_engaged`/`risk.kill_switch_disengaged`
  to the durable audit trail and bump `kill_switch.engaged`/
  `kill_switch.disengaged` counters; the dashboard requires explicit
  confirmation before tripping or re-arming.
- **WP12 — causal attribution is now a read-only regulator view** on the
  dashboard: date-window replay of `/v1/attribution/replay` renders the
  signal → decision → order → fill chain with per-fill realized PnL, blocked
  reasons, and steps. The panel is display-only; review remains an operator
  step in the G-07 loop.
- **WP13 — the evidence drills are now a CI gate.** All 18 credential-free
  drills run in the `evidence-drills` CI job and the build fails if any drill
  regresses; the 7 key-gated drills (live credentials / managed Vault /
  Postgres / public-market) are asserted out of the deterministic CI drill job
  so one can never silently join (credential-gated ones stay operator-run;
  network-gated ones are exercised by the test suite when reachable). This
  closes the "drills run locally only" gap.
- These scores supersede the older 96%/PRI-74 dashboards where they conflict:
  PRI-style indices measure architecture+process completion; this register
  measures *what must be true before real capital moves*.
- **Sprint 45 — WS-resync reconciliation vs live Binance API proven.** The
  final G-02 residual ("WS resync vs live API untested") is closed with live
  evidence: the collector now fires a `on_reconnect` hook after the first
  ingested frame following ≥1 failed connection attempt, and reconciles the
  live cache against Binance REST klines — filling interior gaps and replacing
  diverging candles beyond a 5 bps tolerance (failures metered, never silent),
  with a rate-limited mop-up pass that re-verifies once the damaged candle's
  official kline has matured at the exchange. The live drill forced 3 real
  websocket outages on the wire: 2 resyncs fired, the incomplete post-outage
  candle was detected (divergence) and healed by the mop-up pass, and final
  convergence was gapless with every cached candle matching its official kline
  within tolerance — **VERDICT PASS** (`2026-08-22_ws_resync_drill.log`).
  Full suite **2293 passed / 7 skipped, 100.00% coverage**, `ruff`/`black`/
  `pyright` clean. This closes the resync residual but does **not** close G-02's
  full 72h soak window (still running, ends ~2026-08-25T07:56Z) or G-01's
  real-edge proof — honesty preserved.
- **Sprint 46 — verification-and-close: backup/restore vs current Postgres,
  rate-limiter load-shedding, SIGTERM flake, README.** (1) The post-migration
  **backup→restore drill now runs against the live production Postgres** (schema
  v9, 35 tables): timed `pg_dump -Fc` snapshot restored into a throwaway
  scratch DB with per-table counts matching the backup-time fingerprint,
  scratch dropped, no production data touched — VERDICT PASS
  (`2026-08-22_postgres_backup_restore_drill.log`). It surfaced two real
  findings: an **orphaned `postgres-volume`** (84 MB, detached) left by the
  Amsterdam migration beside the active volume, and the **production image
  shipped no `postgresql-client`** (so `traderos db backup` was impossible in
  production) — the Dockerfile now installs `postgresql-client-18` from PGDG
  to match Railway's PG 18.6 (pg_dump < 18 refuses a newer server with a
  version mismatch). (2) The **rate-limiter burst/load-shedding drill**
  (13/13 PASS) exposed two real defects on the live path and fixed them: a
  load-shedding rejection is no longer an unhandled crash (`RateLimitExceededError`
  added to `_CYCLE_EXCEPTIONS`) and no longer trips the broker circuit breaker
  (`non_failure_exception` on `BROKER_CB`), and HTTP 429s now carry
  `Retry-After`/`X-RateLimit-Limit`/`X-RateLimit-Remaining` per RFC 6585/9110.
  (3) The Sprint 29 "SIGTERM under load, not reproduced" flake is **root-caused
  and closed**: the stop-signal handlers were installed only at the top of
  `_run_forever_loop` — after crash-recovery/reconciliation — so a SIGTERM in
  that window hit the default handler and killed the process; handlers now
  install at the first statement of `run_forever`, with a deterministic
  regression test proving install-before-startup and real-signal survival
  (`2026-08-22_sigterm_startup_drain.log`; 44/44 ×10 under load). These close
  verification gaps only; G-02's 72h soak window and G-01's real-edge proof
  remain open on their own timeline.
- **Sprint 36 — execution-safety hardening (fail closed on the real path).**
  Three Pareto order-path gaps closed, each proven through the *real*
  submission/reconciliation path, not a shared helper: (Gap 3) a
  `FatalExceptionHandler` freeze rail installed by `DaemonController` that
  broadcasts diagnostics, flattens via the true broker path, and **always**
  `sys.exit(1)` even if alerting or flattening failed; (Gap 2) the broker rate
  limiter is now **on by default** (fail closed — opt out only with explicit
  `BROKER_RATE_LIMIT_ENABLED=false`), while the emergency flatten
  `place_flatten_order` bypasses throttle + size guardrail but **stays** under
  the circuit breaker and remains journaled; (Gap 1) startup **and every
  periodic** broker-state reconciliation now run against **real local
  positions/orders** via an optional `local_state_provider` (orch →
  `position_repo.list_open()` + `trade_repo.get_open()` filtered to real broker
  `external_order_id`s, so pending synthetic ids never false-block), and a
  provider failure or unverifiable local view fails closed. Full suite **2193
  passed / 7 skipped, 100.00% (0 missing of 12,453 statements)**, `pyright`
  0 errors in `src/traderos/`. These close concrete rails; they do **not**
  close G-02's full-window paper soak or G-01's real-edge proof — reconcile
  accuracy is bounded by local-journal and broker-snapshot truth, and the
  flatten bypass is *by design* throttled-free, so any future chain layer must
  decide explicitly whether the flatten bypasses it.
- **Sprint 35 — 100% coverage is suite-measured, and account qualification is
  NO-GO.** The full suite runs **2139 tests / 7 skipped** at **100% line
  coverage** (0 missing of 12,139 statements) with `fail_under = 100`; the
  remaining skips are PG-gated tests that still skip when Postgres is
  unreachable, so 100% means *everything runnable in the environment* is
  covered, never a claim about un-runnable paths. Account qualification
  verdict: **NO-GO for real capital** (no funded live account). Alpaca **paper**
  and Binance **testnet** credentials were provided in-process only for
  drills — never written to the repo, and **should be rotated**. MT5 is
  deferred until an account is opened. Closing coverage does not close G-02's
  full-window paper soak or G-01's real-edge proof.
- **Sprint 41 — product-completeness software gaps closed.** Four
  software-closeable slices were closed on the road to GO: (1) `POST
  /v1/backtest` is now honest (real ingested candles, fail-closed on unknown/
  empty symbol) — no UI can display fabricated-in-place numbers; (2) the
  research store is now durable (SQLite wired + new Postgres repos) instead of
  in-memory-and-lost-on-restart; (3) list endpoints are paginated
  (`limit`/`offset`); (4) the real Binance feed is switchable on a deployed
  instance via `BINANCE_ENABLED`/`BINANCE_STREAMING` env vars without editing
  committed YAML. The audit corrected two stale readiness-doc claims (operator
  login WP8 and market/research WP9 were already shipped). These close the
  **fabricated-backtest** and **ephemeral-research** trust gaps but do **not**
  close G-02's full-window paper soak, G-01's real-edge proof, or the
  operator-run gates (managed Vault rotation, live on-call delivery). The
  execution/order path was not touched — real order execution is tested last
  per directive.
- **Sprint 42 — durability completion.** Three more software-closeable gaps
  closed: (1) the **knowledge graph** was in-memory-only and lost on restart —
  now wired to durable SQLite and new Postgres repos (`postgres/knowledge.py`),
  closing the last ephemeral-store gap in the research/knowledge layer; (2)
  **migration v009** added the canonical `experiments`, `experiment_results`,
  `knowledge_nodes`, `knowledge_edges` tables so a fresh Postgres schema
  (applied through migrations) matches what the repos read/write — verified on
  PG 16 to schema version 9; (3) **backtest results are now persisted** through
  the strategy catalog and surfaced via `GET /v1/backtest/history`, so a
  strategy's backtest history is retained across restarts instead of being
  computed-on-the-fly. These close the **ephemeral-knowledge-graph** and
  **non-retained-backtest** trust gaps. They do **not** close G-02's
  full-window paper soak, G-01's real-edge proof, or the operator-run gates
  (managed Vault rotation, live on-call delivery). The execution/order path was
  not touched — real order execution is tested last per directive.
- **Slice 4 (real feed on deploy): code-complete, activation blocked by
  infrastructure.** The deployed instance was silently feedless because the
  `websockets` package shipped in no dependency group; that is fixed and both
  streaming seams now warn loudly instead of dropping silently. The Railway
  instance still cannot reach Binance (REST + WSS) from its egress region —
  local drills prove the full path works on unrestricted egress. Activation is
  an operator dashboard action (region move to EU); no fabricated data is
  served meanwhile (`/v1/market/candles` fails closed with 404).

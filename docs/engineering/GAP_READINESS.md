# TraderOS — Gap-Readiness Checklist & Build Order

**Generated:** 2026-08-04 · **Branch:** `main` · **HEAD:** `cd26366`
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
| G-01 | Backtest realism | 50 | HIGH | Real Alpaca+Binance data, durable store, engine fills+metrics (`sprint23_real_backtest_alpaca_binance.log`) | Fees, slippage, latency; walk-forward/out-of-sample; no cost-adjusted edge proof | A strategy that shows positive expectancy *after* full costs on out-of-sample real data (withheld window) |
| G-02 | Live order ops: ack/timeout, partial fills, reconnect, broker↔journal reconcile | 75 | CRITICAL | Durable journal, restart + Binance + Postgres drills, per-order risk gate, caller-owned `client_order_id` threaded end-to-end (dedupe on restart, intent-idempotency), 300-cycle forced-disconnect soak through the real submission path — 0 duplicates/0 lost/0 reconcile mismatches (`sprint25_paper_soak.log`) | Still on a simulated broker; **real Alpaca paper** ack/timeout + partial fills + WS resync untested; long unattended window | Unattended **paper-broker soak (Alpaca paper, 24–72h)**: 0 reconcile errors, 0 duplicate/lost orders across forced disconnects, journal-recovery replays correctly |
| G-03 | Portfolio-level risk rails | 45 | HIGH | Per-order notional + daily-loss gate (2%-of-equity fail-closed) at submission seam, now keyed to the caller's `client_order_id` (one decision = one gate = one order) | Portfolio caps (total exposure/leverage), kill-switch → flatten orders, data-gap circuit breaker, symbol/notional allowlists | A drill where a portfolio cap and a kill-switch-flatten each provably stop the live loop; allowlist blocks an unlisted symbol |
| G-04 | Firm ops: HA/supervision, alerting, key management, release signing | 35 | HIGH | Docker/compose/Railway/CI; audit trail; env-only paper keys; supervision wiring in daemon + orchestrator with unclean-shutdown detection; **CRITICAL alert proven on forced process kill** (`test_supervision.py` + kill drill); release signing + live-gate scripts (`scripts/governance/`) | No HA failover for the daemon, no secret-manager + rotation, no alerting/on-call platform | Alert delivered on a forced process kill; HA failover proven; live keys live in a secret manager with rotation + access audit |
| G-05 | Causal trade accountability | 60 | MEDIUM | Audit log (`risk.*`, `trade.executed`); `CycleExecutor` records signal → decision → order → fill causal chain (signal_id-keyed), replay service reconstructs per-strategy FIFO realized PnL (`sprint25_causal_replay.log`: 6 real-path cycles, chain integrity verified) | No long-horizon replay across broker restarts; attribution not yet surfaced to a UI/regulator view | Replay a full trading day and reconstruct *why* each fill happened, bit-identical to the recorded events |
| G-06 | Test realism / oracle | 50 | MEDIUM | 1328 tests, 92%+ coverage, conformance + dependency-direction tests, forced-disconnect + supervision + secret-hygiene + causal-replay + governance drills | No withheld-data conformance run; no reference-PnL oracle for a known strategy | A known strategy run through a frozen dataset reproduces a committed reference PnL to a stated tolerance |
| G-07 | Governance for real capital | 75 | MEDIUM | Constitution, ADRs, release constitution, sprint docs, **`LIVE_RUN_POLICY.md`** (red-lines, kill authority, env separation, credential policy, pilot terms), HMAC **release signing** + fail-closed **live gate** scripts with tests (`test_live_gate_governance.py`) | No operator acknowledgement recorded against the policy; no secret-manager integration; live gate not yet wired into CI | A documented live-run policy (red-lines, kill authority, release signing) reviewed and committed before any real capital |

---

## Build order (risk-rated sequence)

| Order | Work | Risk | Why here | Size |
|---|---|---|---|---|
| 1 | **G-02 paper-broker soak** (real Alpaca paper, unattended) | CRITICAL | The GO/NO-GO milestone — proves placement→fill→reconcile→recover under real broker behavior | 1–2 days + soak window |
| 2 | **G-01 backtest realism** (fees/slippage/latency + walk-forward) | HIGH | Must exist to validate any strategy *before* the pilot; otherwise the pilot trades an unproven edge | 1–2 days |
| 3 | **G-03 portfolio risk rails** (caps, kill-flatten, allowlists) | HIGH | Real capital must not move without portfolio-level fail-safes | 1 day |
| 4 | **G-04 alerting + HA + keys** (parallel with 3) | HIGH | Live unattended operation without these is how losses go unobserved | 1–2 days |
| 5 | **G-06 oracle/conformance** | MEDIUM | Locks backtest correctness against a frozen reference before pilot | 1 day |
| 6 | **G-05 causal replay** | MEDIUM | Trader/regulator accountability; cheap once G-02 replay exists | 0.5 day |
| 7 | **G-07 governance red-lines + release signing** | MEDIUM | Cheap, do now so the GO/NO-GO has a policy to invoke | 0.5 day |

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

- Closing G-02 does **not** close G-01, G-03, G-04, G-05, G-06, or G-07.
- The Sprint 24 gate (order-level risk) moved risk readiness from ~10 → ~40 on
  G-03's *position-level* sub-item only. Portfolio-level risk remains open.
- Sprint 25/26 (idempotent submit + forced-disconnect soak) ran against a
  simulated broker that exercises the **real submission path** end-to-end
  (journal → guardrail → rate limiter → adapter → paper service). That is not
  yet a real Alpaca paper endpoint soak: ack/timeout and partial-fill behavior
  against the live API is the remaining G-02 exit test.
- The G-04 "alert on kill" is proven in-process (supervision drill + tests);
  an on-call transport and HA failover are still open.
- Backtest fills are still cost-free; any "edge" today is unvalidated.
- These scores supersede the older 96%/PRI-74 dashboards where they conflict:
  PRI-style indices measure architecture+process completion; this register
  measures *what must be true before real capital moves*.

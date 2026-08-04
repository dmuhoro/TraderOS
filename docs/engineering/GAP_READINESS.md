# TraderOS — Gap-Readiness Checklist & Build Order

**Generated:** 2026-08-04 · **Branch:** `main` · **HEAD:** `40fcc2d`
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
| G-02 | Live order ops: ack/timeout, partial fills, reconnect, broker↔journal reconcile | 35 | CRITICAL | Durable journal, restart + Binance + Postgres drills, per-order risk gate (`sprint24_risk_gate_submission_boundary.log`) | Real-broker behavior: ack/timeout handling, partial fills, WS disconnect + state resync, full broker↔journal reconciliation | Unattended **paper-broker soak (Alpaca paper, 24–72h)**: 0 reconcile errors, 0 duplicate/lost orders across forced disconnects, journal-recovery replays correctly |
| G-03 | Portfolio-level risk rails | 40 | HIGH | Per-order notional + daily-loss gate (2%-of-equity fail-closed) at submission seam | Portfolio caps (total exposure/leverage), kill-switch → flatten orders, data-gap circuit breaker, symbol/notional allowlists | A drill where a portfolio cap and a kill-switch-flatten each provably stop the live loop; allowlist blocks an unlisted symbol |
| G-04 | Firm ops: HA/supervision, alerting, key management, release signing | 15 | HIGH | Docker/compose/Railway/CI; audit trail; env-only paper keys | No HA failover for the daemon, no alerting/on-call, no secret-manager + rotation, no signed releases | Alert delivered on a forced process kill; HA failover proven; live keys live in a secret manager with rotation + access audit |
| G-05 | Causal trade accountability | 30 | MEDIUM | Audit log (`risk.*`, `trade.executed`) | No replayable causal chain: signal → decision → order → fill → PnL attribution per strategy | Replay a full trading day and reconstruct *why* each fill happened, bit-identical to the recorded events |
| G-06 | Test realism / oracle | 45 | MEDIUM | 1282 tests, 92.5% coverage, conformance + dependency-direction tests | No withheld-data conformance run; no reference-PnL oracle for a known strategy | A known strategy run through a frozen dataset reproduces a committed reference PnL to a stated tolerance |
| G-07 | Governance for real capital | 20 | MEDIUM | Constitution, ADRs, release constitution, sprint docs | No research-vs-live environment separation, no signed-release gate, no documented live-run red-lines / kill-zero policy | A documented live-run policy (red-lines, kill authority, release signing) reviewed and committed before any real capital |

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
- Backtest fills are still cost-free; any "edge" today is unvalidated.
- These scores supersede the older 96%/PRI-74 dashboards where they conflict:
  PRI-style indices measure architecture+process completion; this register
  measures *what must be true before real capital moves*.

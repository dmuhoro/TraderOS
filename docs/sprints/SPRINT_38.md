# Sprint 38 — Market Brain (Slice A): tick-fed chart watcher wired into the async execution path

**Period:** 2026-08-13
**Objective:** Give the Custom Expert Advisor a real "chart watcher". The Market
Brain is a per-market, tick-fed domain service that reads history + live ticks
into a `StateSnapshot` (regime, trend stage, volatility percentile, momentum,
RSI, liquidity) and returns ranked `Advice` whose risk fraction is hard-capped.
When wired into the async daemon it sits directly **in front of the real
submission path**: an unreadable or below-threshold market **blocks the cycle**
on the actual seam, with an explicit, audited reason — no silent drops, no
fabricated edge.

Scope is deliberately tight (Slice A): a domain service + its wiring into the
already-proven `AsyncDaemonController` decision point. No broker, risk-rule, or
backtest-engine changes.

---

## Proof-first (red) — the evidence shape

Before any implementation, `tests/test_market_brain_service.py` pinned the
contract and failed at import (the service did not exist). The proofs that now
guard the feature run against the **real** submission path:

1. **Fail closed at the seam**: while the Brain has no read (empty or
   insufficient data) it is UNKNOWN, yields NO moves, and wired into the async
   daemon a fresh tick **never invokes the real cycle/broker seam**
   (`stage_role.execute.call_count == 0`), with `async_daemon.brain_cycles` and
   `async_daemon.brain_blocks` counted and a `brain.advice` event published.
2. **Real signal**: fed a real 220-bar history plus live ticks, the Brain
   produces a known `StateSnapshot` and ranked `Move`s whose `risk_fraction`
   NEVER exceeds the configured cap — including under extreme volatility.
3. **Brain allows -> real submission**: with a readable market the async daemon
   runs the real `CycleExecutor.run` exactly once, publishes a `brain.advice`
   event carrying `direction/confidence/risk_fraction`, and `get_status()`
   reports the brain (`brain.advised == 1`).

---

## The real constraint discovered (dependency direction, enforced not assumed)

The first implementation typed the Brain's tick input directly against the
infrastructure `Tick` — and the **dependency-direction architecture test
failed** (`domain` must not import `infrastructure`). The fix keeps the domain
pure: a `_PriceTick` `Protocol` (price/quantity/exchange_timestamp) is what the
service consumes; the async daemon hands it the real infrastructure `Tick`
(structurally compatible). The gate is exercised by the suite, not by
assertion.

A second finding: `synthetic_candles` emits 220 bars **all at the same
timestamp** — a naive `seed_candles` dedupe-by-timestamp collapsed the whole
history to one bar and the Brain could never read it. Seeding now dedupes by
bar identity (timestamp + full OHLCV + timeframe) so a synthetic tape stays
intact and a replay tape stays idempotent, and reads are strict index-based.

## What was built

- `domain/services/market_brain_service.py` — `MarketBrainService`:
  - `seed_candles(market_id, candles)` — idempotent history ingestion (bar
    identity, index-ordered).
  - `update_tick(market_id, tick)` — live-tick ring buffer (liquidity) +
    interval candle aggregation into the read series.
  - `snapshot(market_id)` — `StateSnapshot` built from `AnalysisService`
    indicators (EMA20/50, ATR14, RSI14, Bollinger 20); trend stage from EMA
    alignment (`markup/accumulation/distribution/markdown`), momentum over the
    configured window, ATR percentile vs its own history, regime derived from
    stage (volatility only names the regime when stage is unreadable).
  - `advise(market_id)` — ranked `Advice`. **Fail closed**: insufficient data ->
    "brain warming up"; range-bound/unknown stage -> "no directional edge";
    confident trend below `action_threshold` -> explicit refusal. Every allowed
    `Move` carries `direction`, clamped `confidence`, and
    `risk_fraction <= max_risk_fraction` (volatility only ever **reduces** size,
    never raises it).
- `application/async_daemon.py` — `AsyncDaemonController` now accepts a
  `brain`; a fresh tick is fed to the Brain **before** the cycle, and a blocked
  read audits `async.brain.blocked`, publishes the `brain.advice` event, and
  returns — the real `CycleExecutor.run` is never reached. An allowed read
  audits `async.brain.advice`, metrics `brain_advised`, publishes the event with
  the move, then runs the real cycle. `get_status()` surfaces brain state.
- `tests/test_market_brain_service.py` — fail-closed seam proofs, real-signal
  reads (bullish/bearish/accumulation/distribution/oscilacing/flat/high-vol),
  cap-and-refusal edge cases, event flow, and daemon gating both ways.

## Quality gates (measured, not declared)

- Full suite: **2223 passed, 7 skipped** (up from 2207/7).
- Coverage: **100.00%** — 0 missing of 12,809 statements; gate remains
  `fail_under = 100`.
- `pyright` on the new/changed modules: **0 errors, 0 warnings** (strict).
- `ruff check src/ tests/`: **clean**; `black` and `isort`: **clean**.

## Honesty notes

- The Brain's `StateSnapshot` is computed from a per-market in-memory view; it
  does **not** persist or survive a restart, and seeded history must be re-fed
  (data-ingestion replay hook). Persistence is a future layer.
- The advice is a directional (long/short) + risk-fraction *read*, not an order
  instruction. Order sizing, entry/exit rules, and any live execution remain
  the EA's/strategy's decision and go through the same broker chain as before.
- "Closing the Brain gap" means the async tick path is now fronted by a real
  market read; it does **not** mean all execution risk is complete. The sync
  `DaemonController` path still does not run the Brain, and no live data feed
  has been exercised.

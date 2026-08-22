# Sprint 43 — Launch-prep infrastructure: region migration, gated deploys, restart policy, live-feed activation

**Period:** 2026-08-22
**Objective:** Execute the launch-preparation sequence from `GAP_READINESS.md`
as one dependency-ordered infrastructure sprint. Everything here is
**infrastructure and operator gating only — zero changes to the execution /
order path.**

---

## 0. Dependency-ordered master plan

The sprint's work items were sequenced by hard dependency, not by preference:

```
L2  Region move (US sfo -> EU ams)          <- everything depends on Binance egress
L1  Restart policy + gated auto-deploys     <- independent, executed while L2 migrates
L3  Feed health proof (real Binance candles) <- depends on L2
L4  Paper-broker credential wiring           <- depends on L3 (operator gate)
L5  24-72h cloud paper soak kickoff (G-02)   <- depends on L3 + L4
```

## 1. Region migration via Railway API (L2)

Two prior dashboard "snapshot & patch" attempts did not take effect: the CLI
kept reporting `region: sfo` for TraderOS, and the Postgres instance carried
`region: null` (workspace default = US). Root cause is irrelevant once the fix
is deterministic: **Railway's GraphQL API (`backboard.railway.app/graphql/v2`)
accepts `serviceInstanceUpdate` with an explicit `region`**, which triggers the
same server-side snapshot-migrate-patch flow as the dashboard.

Workspace-available regions: `pdx, ams, sfo, iad, sin`. Amsterdam (`ams`) is
the EU choice — Binance REST/WSS are reachable from NL egress.

Applied mutations:

- `Postgres-gKbz` (`a64c0d78…`) → `region: "ams"` first, so the durable stores
  settle before the app redeploys against them.
- `TraderOS` (`8232aa41…`) → `region: "ams"`, plus explicit
  `restartPolicyType: ON_FAILURE`, `restartPolicyMaxRetries: 10`.

Post-move co-location check: both services must report `ams`; cross-region DB
round-trips (~80 ms transatlantic) would otherwise tax every query.

## 2. Restart policy codified (L1a)

`railway.toml` now declares the deploy policy explicitly so config-as-code and
the live service instance agree:

```toml
[deploy]
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 10
```

This matches the app's own crash-recovery design: a crashed container comes
back with durable state (Postgres + volume-backed SQLite) instead of staying
down until a human notices.

## 3. Gated automated deploys (L1b)

`.github/workflows/ci.yml` gained a `deploy` job that:

- runs **only after every quality gate passes**
  (`version-check`, `lint`, `typecheck`, `test`, `evidence-drills`, `security`);
- fires **only on pushes to `main`**;
- deploys via the Railway CLI using a `RAILWAY_TOKEN` repository secret;
- **skips loudly (green, with a notice) when the secret is absent**, so the
  branch never goes red before the token is provisioned — but also never
  pretends a deploy happened that did not.

No deploy path exists that bypasses the test gates; no silent skips.

## 4. Live-feed activation proof (L3)

After migration completes: start the orchestrator against the EU deployment,
then prove real market data end-to-end through the public read path
(`GET /v1/market/candles?symbol=BTCUSDT`), including a tick-freshness delta to
distinguish live WS flow from stale backfill. Evidence log:
`docs/evidence/2026-08-22_region_migration_feed_activation.log`.

## 5. Operator gates remaining after this sprint

Honest ledger of what code cannot do by itself:

| Gate | Owner | Status |
| --- | --- | --- |
| Alpaca paper keys (G-02 soak prerequisite) | Operator | pending — account-bound secrets; set via `railway variables --set` once issued |
| RAILWAY_TOKEN GitHub secret | Operator | pending — enables the L1b deploy job |
| G-02 24–72h paper soak | Autonomous | ready to start once L4 lands |
| G-01 real-edge proof | Autonomous | sequenced after G-02 |

---
*Sprint 43 continues below as slices complete.*

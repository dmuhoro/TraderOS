# TraderOS

**Research-first operating system for systematic traders.** Ingest market data,
transform it into analyzable structure, run hypothesis-driven research,
execute strategies under disciplined risk management, record every outcome as
evidence, and grow a knowledge graph with every cycle.

This README is a **living product-state document**: it describes what the
product actually is today (verified against the code and the live deployment),
what it is capable of, what is genuinely proven, and what remains before real
capital may move. It is updated as part of every sprint.

---

## Current status (2026-08-17 — v1.2.0)

| Area | Status | Evidence |
|---|---|---|
| Test suite | **2246 passed / 7 skipped / 100% line coverage** (gate `fail_under = 100`) | CI `test` job, `pytest --cov` |
| Static checks | ruff, black, isort, pyright (strict, 0 errors), pre-commit (10 hooks) | CI `lint` + `typecheck` jobs |
| CI pipeline | **Green end-to-end** (lint, typecheck, test, security, governance, version-check, evidence-drills, deploy-check, docker) | `gh run list` (Sprint 40) |
| Release | `v1.2.0` published: wheel + sdist + GitHub Release + GHCR image `ghcr.io/dmuhoro/traderos:1.2.0` | Sprint 40 |
| Live deployment | `https://traderos-production.up.railway.app` — **auth boundary armed (fail-closed)**, paper mode, health/metrics live | Sprint 40 |
| Evidence drills | 18 credential-free drills run as a CI gate; 7 key/network-gated drills operator-run | `docs/evidence/` (67 logs) |
| Governance | Constitution, ADRs, release constitution, live-run policy, operator acknowledgment, fail-closed live gate | Sprint 40 |

**Honest headline:** the engine is built, tested at 100% coverage, and
deployed behind a verified fail-closed boundary — but the product has **never
run on real market data, never placed a real order, and its research/backtest
surfaces still need the durability and real-data wiring described below.**
Launch posture is **NO-GO for real capital** until the exit tests in
`docs/engineering/GAP_READINESS.md` are empirically met.

---

## What the product does today

### Core loop (proven)
- **Signal → decision → order → fill → reconcile** causal chain, recorded to a
  durable, hash-chained audit trail; replay reconstructs per-strategy FIFO
  realized PnL bit-complete across restarts (`replay_service.py`).
- **Execution engine** with cost model (fee + slippage + latency), partial
  fills, forced-disconnect recovery, broker↔journal reconciliation, and
  intent-idempotent `client_order_id` dedupe on restart.

### Risk rails (proven fail-closed)
- Per-order notional + daily-loss gates, gross-exposure cap, position-size and
  max-position limits, symbol allowlist, and a **persistent kill switch** that
  flattens exactly-once through the real broker path. Production config refuses
  to arm without explicit rails (`resolve_risk_rails`, LIVE refuses boot).
- Data-gap breaker: stops trading on stale or missing data.
- Broker rate limiter on by default (opt-out is explicit); emergency flatten
  bypasses throttle but stays under the circuit breaker and journaled.

### Data
- **Mock collector** (deterministic, default) for offline CI/tests.
- **Binance REST collector** (`binance_collector.py`) and **Binance WebSocket
  streaming feed** aggregated into candles (`streaming_collector.py`) — both
  present and tested, gated behind `data_collection.binance.enabled` /
  `.streaming` (see *Gaps* — off by default in shipped config).
- **Alpaca collector** for broker-market data; frozen Binance 1h snapshot
  committed for reproducible walk-forward evidence (network-free).

### Operator API (FastAPI, 55+ routes under `/v1`)
Workflow, portfolio, positions, orders, trades, equity curve, PnL, kill switch,
preflight, readiness, live/check, strategies (CRUD + lifecycle + compare +
clone), session reports, audit, metrics, manifest, SSE events, per-user
attribution replay, market overview, market candles/symbols, research
indicators/backtest/observations, retail register/login/logout/orders, and
operator auth login/logout.

### Operator dashboard (`/dashboard`)
Single-page static bundle (no build step) serving all panels above, with
**session-token login (username/password, `POST /v1/auth/login`) stored in
`sessionStorage` — never a roaming `localStorage` API key** (WP8), and a
single-use SSE token for the live event stream.

### Research (research lab)
`ResearchService` with observations / hypotheses / experiments / lessons,
indicator toolkit (SMA/EMA/RSI/ATR/Bollinger/Stochastics), and per-symbol
backtests against the live-ingested candle series.

### Security & operations
- Auth: role-scoped API keys (`admin`/`operator`/`viewer`, constant-time
  compare) **or** PBKDF2-backed session login; fail-closed boundary middleware;
  production boot refuses to serve without keys + TLS posture
  (`assert_production_policy`).
- HA: lease-based failover (stale-after-90s takeover, fail-closed standby).
- Secret management: HashiCorp Vault (KV-v2) provider on the live boot path,
  value-redacted access audit, rotation with reload; env fallback (paper).
- On-call: PagerDuty events/v2 + Slack webhook + generic webhook transports,
  env-gated, fail-closed construction; CRITICAL alerts on unclean death, kill
  trip, gap breach.
- Observability: Prometheus `/metrics`, health endpoints, run manifest,
  supervision with unclean-shutdown detection.
- Releases: HMAC-signed, operator-acked, CI-enforced version/tag gates.

### CLI (`traderos`)
`strategies`, `backtest`, `papertrade create|list`, `signal`, `daemon`, `run`,
`status`, `risk` (engage/disengage/reconcile), `metrics snapshot|watch`,
`validate`, `pilot readiness|dry-run`, `security audit`, `db migrate|check|
rollback|backup|restore`, `health`, `audit verify|query`, `notify`.

---

## Known gaps on the road to GO (honest)

Everything in this section is a **measured** gap — each is either closeable in
software, operator-run, or account-gated. The full register with scores and
exit tests lives in `docs/engineering/GAP_READINESS.md`.

### Closeable in software (this sprint work)
1. **`POST /v1/backtest` serves synthetic candles** (`server.py:353-368`) — it
   fabricates a deterministic price ramp and never touches real data. The
   research backtest (`POST /v1/research/backtest`) already uses real ingested
   candles; the plain endpoint must be made honest (or removed).
2. **Research store is in-memory only** — `ResearchService` is wired to
   `InMemory*Repository` (`factory.py:418-424`); observations/hypotheses/
   experiments/lessons are **lost on restart**. Full SQLite implementations
   exist (`sqlite/research.py`) and must be wired; a PG variant is needed for
   the deployed store.
3. **No pagination** — audit/trades/positions/orders/strategies/manifest/
   papertrade sessions/equity-curve/attribution chains return unbounded lists
   (only `limit` on a few routes).
4. **Real feed off in shipped config** — `configs/settings.yaml` sets
   `binance.enabled: false` / `streaming: false`; the deployed instance serves
   mock data. Needs an env-var override and a ≥24h real-feed run with the
   data-gap breaker armed.

### Operator-run (need credentials/time, harness ready)
5. **24–72h unattended Alpaca paper soak** (G-02 exit test) — `run_unattended_paper_soak.py`
   ready and fails closed without keys; bounded runs pass; full window never
   run.
6. **Managed Vault/KMS rotation cadence** — proven against local Vault only.
7. **Live on-call delivery** — transports proven on the wire in drills; no
   real PagerDuty/Slack account has received a live incident.

### Account-gated (deferred last, per directive)
8. **Real order execution** — real Alpaca connectivity proven once (read-only);
   **no real order has ever been submitted or filled**. Tested last, after all
   software and operator gates are green.
9. **A cost-adjusted edge, or an explicit data-validation-only posture** —
   walk-forward shows no positive expectancy after full costs on out-of-sample
   data; the launch claim must be decided honestly before real capital.

---

## Quick start

```bash
# Install
pip install -e ".[all,dev]"

# Configure (copy and edit)
cp .env.example .env

# Run tests (full suite + 100% coverage gate)
make test

# Run the API server (paper mode; auth open only if no keys configured)
traderos-api

# Run the trading daemon
traderos daemon start
```

### Operator auth (production)

Set role keys (or bootstrap an operator account for session login) before
deploying:

```bash
export TRADEROS_ADMIN_API_KEY='<long-random>'
export TRADEROS_OPERATOR_API_KEY='<long-random>'
export TRADEROS_VIEWER_API_KEY='<long-random>'
export TRADEROS_ENV=production
export TLS_TERMINATED_BY_PROXY=true
export TRADING_MODE=paper
```

The API **refuses to boot** in production without keys + a TLS posture
(fail-closed). For the session-login flow: set `TRADEROS_ADMIN_USERNAME` /
`TRADEROS_ADMIN_PASSWORD` on first boot to seed the operator account.

---

## Common operations

```bash
# Paper trading
traderos papertrade create          # start a paper session
traderos papertrade list            # list sessions

# Backtesting (honest: uses real ingested candles via research endpoint)
curl -X POST localhost:8000/v1/research/backtest \
  -H 'Content-Type: application/json' \
  -d '{"strategy": "moving_average_trend", "symbol": "BTCUSDT"}'

# Operator login (session token, not an API key)
curl -X POST localhost:8000/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username": "...", "password": "..."}'

# Dashboard
open http://localhost:8000/dashboard
```

---

## Architecture

```
traderos/
├── domain/            # Pure business logic (entities, services, ports, repositories)
│   ├── entities/      # Trade, Position, Signal, Candle, Strategy, Knowledge, Research
│   ├── services/      # Backtesting, PaperTrading, Risk, Portfolio, Analysis, Research, Replay
│   ├── ports.py       # Protocol interfaces for dependency inversion
│   └── adapters/      # BrokerAdapter ABC
├── application/       # Orchestration layer
│   ├── orchestrator.py     # TradingOrchestrator (per-mode runner)
│   ├── cycle_executor.py   # Per-market cycle logic
│   ├── daemon_controller.py # Lifecycle management + supervision
│   ├── async_daemon.py     # Async tick-driven loop (Pareto WS ingestor)
│   └── factory.py          # DI composition root
├── infrastructure/    # Concrete implementations
│   ├── alpaca_broker.py, journaled_broker.py  # Broker adapters
│   ├── collectors/          # Mock / Binance / streaming / Alpaca / yfinance
│   ├── config/              # Config loader (env vars + YAML)
│   ├── database/            # SQLite + migrations (v001–v008) + backup/restore
│   ├── repositories/        # In-memory / SQLite / Postgres repos
│   ├── secrets.py           # Env + Vault providers, rotation
│   ├── notifiers/           # On-call router, webhook, PagerDuty, Slack
│   ├── ha_failover.py, leader_election.py   # HA
│   └── observability.py, audit.py, metrics.py, monitoring.py
└── interfaces/       # Entry points
    ├── api/          # FastAPI server + dashboard (static SPA)
    └── cli/          # Command-line interface
```

### Migrations (v001–v009)

`v001_initial`, `v002_observability`, `v003_strategies`, `v004_external_order_id`,
`v005_order_event_journal`, `v006_operator_surface`, `v007_historical_candles`,
`v008_user_accounts`, `v009_research_knowledge` (canonical research + knowledge
tables). Backend: SQLite (dev/test), Postgres (production), in-memory (tests).
Research store, knowledge graph, and backtest history are durable on SQLite and
Postgres — not lost on restart.

---

## Key environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `TRADING_MODE` | `paper` | `paper` \| `live` \| `backtest` |
| `TRADEROS_ENV` | `development` | `production` arms the sealed security policy |
| `DB_PATH` | `data/trader.db` | SQLite path (or `DATABASE_URL` for Postgres) |
| `DATABASE_URL` | — | Postgres connection (production store) |
| `DEFAULT_CASH` | `10000.0` | Paper/backtest starting capital |
| `TRADEROS_ADMIN/OPERATOR/VIEWER_API_KEY` | — | Role-scoped API keys (fail-closed in production) |
| `TRADEROS_ADMIN_USERNAME/PASSWORD` | — | Seeds the operator session-login account |
| `TLS_TERMINATED_BY_PROXY` | — | `true` when a PaaS edge (Railway) serves HTTPS |
| `TRADING_MODE` + `data_collection.binance.enabled/streaming` | off | Enable the real Binance feed (see Gaps) |
| `BINANCE_ENABLED` / `BINANCE_STREAMING` | off | Env-var override for the real Binance feed (Railway-friendly) |
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | — | Broker keys (paper or live; prefer Vault in live) |
| `WEBHOOK_URL` / `SLACK_WEBHOOK_URL` / `PAGERDUTY_ROUTING_KEY` | — | On-call transport wiring |
| `RISK_*` | — | Override risk rails; LIVE refuses to arm without them |
| `CORS_ORIGINS` | — | Allowed browser origins for the dashboard API |

Secrets live in env or the secret manager — **never in `settings.yaml`, never
committed**. Paper keys exist only in-process for drills and should be rotated.

---

## Evidence & verification culture

Every sprint ships with committed evidence under `docs/evidence/` (67 logs).
Drills are genuine: they exercise the real code paths (submission seam,
journal, reconciliation, risk rails, governance gate) and either record a
`VERDICT: PASS` with a measurable condition or return a non-zero exit — no
simulated `PASS`. The 18 credential-free drills run as a CI gate
(`evidence-drills` job) so a regression fails the build.

Key documents:

| Document | Purpose |
|----------|---------|
| `docs/engineering/GAP_READINESS.md` | Gap register with scores, risk, exit tests, GO/NO-GO definition |
| `docs/engineering/CONSTITUTION.md` | Engineering constitution (authoritative rules) |
| `docs/engineering/LIVE_RUN_POLICY.md` | Red-lines, kill authority, pilot terms for real capital |
| `docs/engineering/OPERATIONAL_TRUST_REPORT.md` | Operational-trust closure record |
| `docs/runbooks/PILOT_READINESS.md` | Readiness gate + go/no-go for live |
| `docs/runbooks/CONTROLLED_PILOT.md` | Bounded, supervised pilot terms |
| `docs/runbooks/RAILWAY_DEPLOY.md` | Deploy + on-call + rollback runbook |
| `docs/sprints/SPRINT_*.md` | Chronological sprint records |

---

## Development

```bash
git clone https://github.com/dmuhoro/TraderOS.git
cd TraderOS
python3.11 -m venv venv && source venv/bin/activate
pip install -e ".[all,dev]"

make lint          # ruff
make typecheck     # pyright (strict)
make test          # pytest + 100% coverage gate
make format        # black + isort
pre-commit run --all-files
```

## Tech stack

Python 3.11+, FastAPI, SQLite/Postgres, Alpaca-py, Pydantic v2, NumPy, Pandas,
pytest, ruff, black, pyright, pre-commit, Docker, GitHub Actions, Railway,
HashiCorp Vault.

## License

MIT

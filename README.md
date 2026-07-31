# TraderOS

**Research-first operating system for systematic traders.** Paper trading, backtesting, live Alpaca execution — all in one CLI and API.

## Quick Start

```bash
# Install
pip install -e .[all]

# Set up environment
cp .env.example .env
# Edit .env with your config

# Run paper trading
traderos papertrade create

# Run API server
traderos-api

# Run tests
make test
```

## Features

- **Paper Trading** — Full simulation with configurable slippage, partial fills, position management
- **Backtesting** — Historical replay with Sharpe, Sortino, max DD metrics
- **Live Trading** — Alpaca integration for real market execution
- **Risk Management** — Kill switch, circuit breaker, max drawdown, max positions
- **Signal Framework** — Pluggable strategies (MA Trend, Volatility Breakout, Mean Reversion)
- **Strategy Catalog** — Versioned strategy lifecycle (draft → active → promoted/disabled/archived), cloning, comparison ranking
- **Operator Workflow** — Enforced paper→live session lifecycle with a per-step gate on every check
- **Operator Dashboard API** — positions, orders, trades, portfolio, equity curve, pnl, kill switch, preflight, readiness, workflow, strategies, session reports
- **Session Reports** — JSON/Markdown snapshot of a completed operator session
- **REST API** — FastAPI server with health, strategies, backtest, paper trade, audit, metrics
- **CLI** — Unified command-line interface for all operations
- **Observability** — Audit trail (hash-chained), metrics, health checks, run manifest
- **Persistence** — SQLite with versioned migrations, 90-day data archival

## Architecture

```
traderos/
├── domain/          # Pure business logic (entities, services, ports, repositories)
│   ├── entities/    # Trade, Position, Signal, Candle, Strategy, etc.
│   ├── services/    # Backtesting, PaperTrading, Risk, Portfolio, Analysis
│   ├── ports.py     # Protocol interfaces for dependency inversion
│   └── adapters/    # BrokerAdapter ABC
├── application/     # Orchestration layer
│   ├── orchestrator.py     # TradingOrchestrator (per-mode runner)
│   ├── cycle_executor.py   # Per-market cycle logic
│   ├── daemon_controller.py # Lifecycle management
│   └── factory.py          # DI composition root
├── infrastructure/  # Concrete implementations
│   ├── alpaca_broker.py    # Real broker adapter
│   ├── config/             # Config loader (env vars + YAML)
│   ├── database/           # SQLite + migration manager
│   ├── repositories/       # In-memory + SQLite repos
│   └── observability.py    # Audit, Metrics, Health, Manifest
└── interfaces/      # Entry points
    ├── api/         # FastAPI server
    └── cli/         # Command-line interface
```

## Commands

```bash
# Paper trading
traderos papertrade create       # Start a paper session
traderos papertrade list         # List paper sessions
traderos papertrade status       # Show current status

# Backtesting
traderos backtest run <strategy>

# Strategies
traderos strategies list
traderos strategies show <name>

# Observability
traderos health                  # System health
traderos audit                   # Audit trail
traderos metrics                 # Metrics snapshot
traderos signal <market_id>      # Active signals

# API server
traderos-api                     # Start on 0.0.0.0:8000
```

Operator workflow (paper → live) via the dashboard API:

```bash
curl -X POST localhost:8000/v1/workflow/advance -H 'Content-Type: application/json' -d '{"step": "start"}'
curl localhost:8000/v1/workflow        # current step, status, history
curl localhost:8000/v1/positions       # positions, orders, trades, portfolio, equity-curve, pnl
curl -X POST localhost:8000/v1/kill-switch/engage
curl localhost:8000/v1/reports/session # JSON session report (?fmt=markdown for Markdown)
```

## Documentation

| Document | Purpose |
|----------|---------|
| `docs/engineering/FINISH_LINE_DASHBOARD.md` | Authoritative design of the operator surface (workflow, API, catalog, reports) |
| `docs/engineering/CORE_LOOP_TRUTH.md` | Canonical description of the trading core loop |
| `docs/engineering/OPERATIONAL_TRUST_MATRIX.md` | Operational-trust closure record |
| `docs/engineering/MASTER_EXECUTION_PROGRAMME.md` | v1 master execution programme |

## Configuration

All configuration via environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `TRADING_MODE` | `paper` | `paper`, `live`, `backtest` |
| `DB_PATH` | `data/trader.db` | SQLite database path |
| `DEFAULT_CASH` | `10000.0` | Paper/backtest starting capital |
| `MAX_DRAWDOWN` | `0` | Max drawdown % (0 = no limit) |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `TRADEROS_API_KEY` | — | API auth key (optional) |
| `WEBHOOK_URL` | — | Notification webhook URL |
| `ALPACA_API_KEY` | — | Alpaca live trading key |
| `ALPACA_SECRET_KEY` | — | Alpaca live trading secret |

## Development

```bash
git clone https://github.com/dmuhoro/TraderOS.git
cd TraderOS
python3.11 -m venv venv && source venv/bin/activate
pip install -e .[all,dev]

# Run all checks
make lint          # ruff
make typecheck     # pyright
make test          # pytest + coverage (threshold: 70%)

# Individual check
make format        # black + isort
```

## Tech Stack

Python 3.14, FastAPI, SQLite, Alpaca-py, Pydantic, NumPy, Pandas, pytest, ruff, pyright, Docker.

## License

MIT

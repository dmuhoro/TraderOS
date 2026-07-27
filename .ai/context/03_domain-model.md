# 03 — Domain Model

## Purpose
Canonical domain model for TraderOS. Every entity, value object, aggregate, relationship, state transition, and invariant. No other document defines domain entities.

## Authority Level
**Foundational** — source of truth for domain types. All code must conform.

## Consumers
All AI agents, domain engineers, data modelers, test authors.

## Dependencies
- `docs/engineering/CONSTITUTION.md` [C:6 Domain Model]

## Source Documents
- Constitution Section 6
- Existing `database/db_manager.py` schema
- Knowledge graph workflow

## Update Rules
- Update when new entities are added
- Update when relationships change
- ADR required for entity lifecycle changes

---

## Entity Catalog

### Market
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | Primary key |
| symbol | str | Normalized (e.g., BTCUSDT) |
| asset_class | enum | crypto, forex, equity, futures |
| exchange | str | Source exchange |
| status | enum | active, inactive, error |

**Relationships**: Has many Candle, Indicator, Signal, Trade.
**Lifecycle**: Created on first data collection. Never deleted.

### Candle
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | Primary key |
| market_id | UUID | FK → Market |
| timestamp | datetime | Start of period |
| ohlcv | decimal[5] | Open, High, Low, Close, Volume |
| timeframe | str | 1h, 1d |
| source | str | Collector ID |

**Invariant**: `low <= open, close <= high` — always.

### Indicator
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | PK |
| market_id | UUID | FK → Market |
| timestamp | datetime | Computed at |
| name | str | e.g., atr, sma_20 |
| value | float | |

**Lifecycle**: Immutable once computed. Recalculated on data append.

### Regime
| Type | str | trend, ranging, volatile |
| Parameters | window_short, window_medium, window_long | int |
| Current | str | Current regime label |

**Lifecycle**: Re-evaluated each cycle. Not persisted — derived from Indicators.

### LiquidityZone
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | PK |
| market_id | UUID | FK → Market |
| price_level | float | Support or resistance |
| zone_type | enum | support, resistance |
| strength | int | Number of touches |
| detected_at | datetime | |

### Signal
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | PK |
| market_id | UUID | FK → Market |
| strategy_id | UUID | FK → Strategy |
| direction | enum | long, short, neutral |
| confidence | float | 0.0–1.0 |
| generated_at | datetime | |
| expires_at | datetime | |

### Strategy
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | PK |
| name | str | Unique |
| params | dict | JSON |
| version | str | Semver |

**State Machine**: draft → active → deprecated → retired

### BacktestResult
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | PK |
| strategy_id | UUID | FK → Strategy |
| market_id | UUID | FK → Market |
| metrics | dict | total_return, sharpe, win_rate, etc. |
| equity_curve | list[float] | |
| period | (datetime, datetime) | |

### Trade
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | PK |
| signal_id | UUID | FK → Signal |
| market_id | UUID | FK → Market |
| side | enum | buy, sell |
| quantity | float | |
| price | float | |
| status | enum | pending, filled, cancelled, rejected |

### Position
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | PK |
| market_id | UUID | FK → Market |
| quantity | float | Positive = long, negative = short |
| entry_price | float | |
| current_price | float | |
| pnl | float | |

### Research Entities (Observation → Hypothesis → Test → Result → Lesson)
This is the knowledge graph chain:

**Observation**: `{id, timestamp, symbol, content, tags}` — what was noticed
**Hypothesis**: `{id, observation_id, content, status}` — what might be true
**Experiment/Test**: `{id, hypothesis_id, params, results}` — how it was tested
**Result**: `{id, test_id, metrics, visual_path}` — what happened
**Lesson**: `{id, result_id, content, tags}` — what was learned

**Invariant**: Chain must be complete. No dangling references.

## Value Objects
- `OHLCV` — open, high, low, close, volume
- `EquityCurve` — list of (timestamp, equity) pairs
- `Metrics` — dictionary of performance metrics
- `Timeframe` — 1m, 5m, 15m, 1h, 4h, 1d
- `SessionConfig` — name, start_hour, end_hour

## Aggregates
- **MarketData**: Market + Candles + Indicators
- **ResearchWorkflow**: Observation → Hypothesis → Test → Result → Lesson
- **Portfolio**: Positions + RiskProfile + Performance

## State Transitions
```
Strategy: draft → active → deprecated → retired
Order: pending → validated → submitted → filled | cancelled
Hypothesis: proposed → testing → validated | rejected
Lesson: captured → applied → archived
KillSwitch: armed → triggered → reset
```

## Invariants
1. Candle: `low <= min(open, close)` and `high >= max(open, close)`
2. Research chain: Lesson must trace to Observation without gaps
3. Portfolio: Sum of position sizes ≤ max_allocation
4. Risk: current_drawdown < max_drawdown or kill switch triggers
5. Timestamps: All timestamps in UTC with timezone info

## References
- [C:6] Domain Model — full entity catalog with field-level detail
- `.ai/context/05_db-contracts.md` — persistence mapping
- Master Execution Programme §6 — capability map
- WP-009 — domain entity dataclasses implementation

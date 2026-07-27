# 05 — Database Contracts

## Purpose
Database architecture, schema ownership, migration rules, and persistence boundaries. Every repository implementation must conform to these contracts.

## Authority Level
**Enforceable** — violations break migrations and data integrity.

## Consumers
AI agents implementing repositories, database engineers, migration authors.

## Dependencies
- `.ai/context/03_domain-model.md` — entity definitions
- `docs/engineering/CONSTITUTION.md` [C:8.18]
- `docs/adr/ADR-005.md` — SQLite/PostgreSQL strategy

## Source Documents
- ADR-005
- Constitution §8.18
- `database/db_manager.py` — current schema
- `database/migrations/v001_initial.py` — initial migration

## Update Rules
- Update when schema changes
- Update when migration version increments
- ADR required for storage engine change (SQLite → PostgreSQL)

---

## Database Architecture

**Current**: SQLite (single file, file-based locking)  
**Target**: PostgreSQL (connection pool, concurrent access)  
**Migration Path**: ADR-005 — incremental via repository pattern

## Schema Ownership

| Schema Owner | Tables | Authority |
|-------------|--------|-----------|
| Domain | `markets`, `candles`, `indicators`, `signals`, `trades` | Entity definitions |
| Infrastructure | All persistence code | Implementation |
| Research | `observations`, `hypotheses`, `tests`, `results`, `lessons` | Knowledge graph |
| Risk | `risk_limits` | Risk engine |

## Migration Rules

1. Every schema change gets a versioned migration file
2. Migration files: `v{version}_{description}.py`
3. Migrations are immutable once applied
4. Rollback via `down()` function (development only)
5. Migration order is alphabetical (use zero-padded version numbers)
6. Data migrations (column additions with defaults) are preferred over destructive changes
7. Test migrations on a copy of production data before release

## Naming

| Element | Convention | Example |
|---------|-----------|---------|
| Tables | `snake_case`, plural | `market_data` |
| Columns | `snake_case` | `symbol`, `max_drawdown` |
| Primary keys | `id` | INTEGER PK |
| Foreign keys | `{table}_id` | `strategy_id` |
| Indexes | `idx_{table}_{column}` | `idx_market_data_symbol` |
| Unique constraints | `UNIQUE(col1, col2)` | |

## Transactions

- Single-operation writes: auto-commit
- Multi-table writes: explicit transaction
- Research chain operations: single transaction per chain
- Long-running analysis: read-only transaction

```python
# GOOD
def save_workflow(self, observation, hypothesis):
    cursor = self.conn.cursor()
    try:
        cursor.execute("INSERT INTO observations ...", observation)
        cursor.execute("INSERT INTO hypotheses ...", hypothesis)
        self.conn.commit()
    except Exception:
        self.conn.rollback()
        raise
```

## Indexes

| Table | Index | Reason |
|-------|-------|--------|
| `market_data` | `(symbol, timestamp)` | OHLCV queries filtered by symbol+time |
| `features` | `(symbol, timestamp, feature_name)` | Feature lookups |
| `correlations` | `(symbol_a, symbol_b, window_size)` | Correlation queries |
| `journal_entries` | `(timestamp)` | Recent entries |

## Constraints

- Primary keys: EVERY table has `id INTEGER PRIMARY KEY AUTOINCREMENT`
- Unique constraints: Business-key uniqueness enforced at schema level
- Foreign keys: Research chain enforced, others soft-referenced
- NOT NULL: Critical fields only (symobl, timestamp, content)
- CHECK: `low <= high`, `open > 0`, `volume >= 0`

## Repository Pattern

**When implemented** (WP-010+):

```
Domain defines interface:
    class MarketDataRepository(ABC):
        @abstractmethod
        def get_ohlc(self, market_id: str, limit: int) -> list[Candle]: ...

Infrastructure implements:
    class SQLiteMarketDataRepository(MarketDataRepository):
        def get_ohlc(self, market_id: str, limit: int) -> list[Candle]: ...

    class InMemoryMarketDataRepository(MarketDataRepository):  # For tests
        ...
```

**Current state** (pre-WP-010): Direct `DatabaseManager` access. Known debt.

## Persistence Boundaries

| Operation | Allowed direct DB? | Should use |
|-----------|-------------------|------------|
| Domain logic | NO | Repository interface |
| Application orchestration | NO | Domain services |
| CLI commands | NO | Application layer |
| Infrastructure | YES | Repository implementation |
| Migrations | YES | Migration files |
| Tests | In-memory only | InMemoryRepository |

## References
- ADR-005 — SQLite/PostgreSQL strategy
- `.ai/context/03_domain-model.md` — entity definitions
- Constitution [C:8.18] — automated versioned migrations
- Master Execution Programme WP-007 — migration framework
- Master Execution Programme WP-010 — repository interfaces

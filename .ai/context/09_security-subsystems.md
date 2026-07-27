# 09 — Security Subsystems

## Purpose
Security responsibilities for every subsystem. Attack surfaces, mitigations, and monitoring requirements per module.

## Authority Level
**Enforceable** — each subsystem must implement its security controls.

## Consumers
AI agents implementing specific subsystems, security reviewers.

## Dependencies
- `.ai/context/08_security.md` — global security policy

## Source Documents
- Constitution §4.4
- Existing codebase analysis

## Update Rules
- Updated when new subsystems are added
- Updated when attack surface changes

---

## Market Data Pipeline

**Attack Surface**: External API keys, raw market data, injection via symbol names

**Mitigations**:
- API keys via env vars, never in code
- Symbol validation (uppercase, alphanumeric + selected special chars)
- Volume normalization (reject negative/zero)
- Timeout on external API calls (30s default)
- Retry with exponential backoff (max 3 retries)

**Monitoring**: Data staleness alerts, collection failure rate

## Database Layer

**Attack Surface**: SQL injection, file path traversal, data corruption

**Mitigations**:
- Parameterized queries — ENFORCED (CI regex check)
- No dynamic SQL construction
- Migration rollback capability
- Transactional writes for research chains
- WAL mode for SQLite

**Monitoring**: Migration failures, constraint violations

## Risk Engine

**Attack Surface**: Financial loss via incorrect calculations, kill switch bypass

**Mitigations**:
- Position size bounded by hard-coded maximum (50% of capital)
- Kill switch is always-armed (checks every cycle, no disable)
- Drawdown check before every trade
- Kill switch triggers at configurable threshold, defaults to -15%

**Monitoring**: Kill switch events logged at WARNING level

## Strategy Lab

**Attack Surface**: Malicious strategy code, resource exhaustion

**Mitigations**:
- Strategy parameters are data, not code (JSON config)
- Max backtest duration enforced
- Strategy registry is curated (no ad-hoc loading)
- Signal generation bounded by symbol availability

**Monitoring**: Strategy crash rate, computation time

## Research Engine

**Attack Surface**: Data loss, inconsistent knowledge graph

**Mitigations**:
- Research chain operations in single transactions
- Foreign key constraints enforced at schema level
- No orphaned research entities (verified by invariant tests)
- Lesson traceability enforced (every lesson links to observation)

**Monitoring**: Chain completeness (cron check for dangling references)

## CLI Interface

**Attack Surface**: Command injection, path traversal

**Mitigations**:
- argparse for all input parsing (no raw input() in production)
- File paths validated and restricted to allowed directories
- JSON output mode for programmatic use (prevents terminal injection)

## API (Future)

**Attack Surface**: Authentication bypass, rate limiting, injection

**Mitigations** (planned):
- API key authentication
- Rate limiting per key
- Input validation via Pydantic
- CORS restricted to known origins
- Request logging for audit trail

## Visualization

**Attack Surface**: Information leakage via charts

**Mitigations**:
- No sensitive data in chart titles/labels
- Export directory is configurable
- Chart generation is stateless (read-only from DB)

## References
- `.ai/context/08_security.md` — global security policies
- Constitution §4.4 — Fail Closed principle

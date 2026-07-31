# 08 — Security

## Purpose
Security philosophy, threat model, and engineering controls for TraderOS. Defines how we handle secrets, validation, and incident response.

## Authority Level
**Enforceable** — security violations block releases.

## Consumers
AI agents, engineers, DevOps, security reviewers.

## Dependencies
- Constitution [C:4.4] — Fail Closed principle
- `.ai/context/09_security-subsystems.md` — subsystem-specific security

## Update Rules
- Reviewed quarterly
- Updated immediately when vulnerability is discovered
- ADR required for crypto/authentication changes

---

## Security Philosophy

**Fail Closed**: When any subsystem cannot determine the correct action, it defaults to the safest option. For risk: reduce position. For execution: cancel order. For data: skip symbol. For research: log warning, continue.

**Defense in Depth**: Multiple layers of validation. No single point of trust.

**Least Privilege**: Every component has only the permissions it needs.

## Threat Model

| Threat | Likelihood | Impact | Mitigation |
|--------|-----------|--------|------------|
| API key leak | Medium | Critical | .env never committed, env vars only, audit logging |
| Injection via symbol names | Low | High | Parameterized queries only, no string concatenation |
| Rogue strategy | Low | Medium | Sandboxed execution, kill switch override |
| Data corruption | Medium | High | Transactional writes, migration rollbacks, backups |
| Supply chain (pip) | Low | High | Locked requirements, review deps quarterly |
| DoS via large datasets | Low | Low | Pagination, query limits, timeout on external calls |

## Secrets

- NEVER commit secrets, keys, or passwords
- `.env` is in `.gitignore` (use `.env.example` for template)
- Production secrets via environment variables only (Docker secrets or vault)
- API keys stored outside code, loaded via `config_loader.py`
- Key rotation: documented in runbook, automated where possible

## Authentication

**Current state**: No authentication (local-only CLI).
**Target state**: API keys for REST API, JWT for sessions (Post-MVP).

## Authorization

**Current state**: CLI user has full access to local database.
**Target state**: Role-based access (admin, researcher, trader, viewer) for multi-user.

## Validation

- All external inputs validated at the boundary (CLI args, API params, file paths)
- SQL injection: parameterized queries ONLY. NEVER f-string concatenation.
- CSV/file injection: strip control characters from external inputs
- Symbol normalization: uppercase, strip whitespace, validate against known exchanges

```python
# GOOD
def get_ohlc(self, symbol: str, limit: int = 1000) -> pd.DataFrame:
    query = "SELECT * FROM market_data WHERE symbol = ? ORDER BY timestamp ASC LIMIT ?"
    return pd.read_sql_query(query, self.conn, params=[symbol, limit])

# BAD — NEVER DO THIS
def get_ohlc(self, symbol: str, limit: int = 1000):
    query = f"SELECT * FROM market_data WHERE symbol = '{symbol}' LIMIT {limit}"
    return pd.read_sql_query(query, self.conn)
```

## Logging

- Log security events (failed auth, kill switch trips, exposure breaches) at WARNING level
- Never log keys, passwords, or tokens
- Log structure: `{"event": "...", "severity": "...", "timestamp": "...", "context": {...}}`
- Audit trail: all trade operations, configuration changes, kill switch events

## Incident Response

| Severity | Response Time | Escalation | Actions |
|----------|--------------|------------|---------|
| Critical | < 1 hour | CTO, Security Lead | Isolate, rotate keys, patch, post-mortem |
| High | < 4 hours | Tech Lead | Fix, test, deploy, review |
| Medium | < 48 hours | Engineer | Fix in next sprint |
| Low | Next planning | Team | Log and track |

## References
- [C:4.4] Engineering Philosophy — Fail Closed
- `.ai/context/09_security-subsystems.md` — per-subsystem security
- Master Execution Programme §15 — Risk Register

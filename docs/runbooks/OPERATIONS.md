# Operations Runbook — TraderOS

## Backup & Restore

### Automated Backup

Backups run on startup via `purge_old_entries` in `_get_db()`.  Manual backup via CLI:

```bash
# SQLite
traderos db backup                           # creates gzip under backups/

# PostgreSQL (requires pg_dump)
DATABASE_URL=postgresql://... traderos db backup
```

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_BACKUP_DIR` | `backups/` | Backup output directory |
| `DB_MAX_BACKUPS` | `30` | Number of backups to retain (oldest rotated) |

### Restore

```bash
# SQLite
traderos db restore backups/sqlite_20260729_120000.sqlite.gz

# PostgreSQL
traderos db restore backups/postgres_20260729_120000.dump
```

The restore command detects backend from `DATABASE_URL`.

### Verify Backup Integrity

```bash
# SQLite — check gzip integrity
gunzip -t backups/sqlite_*.sqlite.gz

# PostgreSQL — test dump can be listed
pg_restore --list backups/postgres_*.dump | head -20
```

---

## Incident Response

### Severity Levels

| Level | Definition | Response Time |
|-------|------------|---------------|
| SEV-1 | Trading halted or data loss | 15 min |
| SEV-2 | Feature degraded, no data loss | 1 hour |
| SEV-3 | Minor,不影响核心功能 | Next business day |

### Incident Lifecycle

1. **Detect** — alert from health check, metric anomaly, or user report
2. **Triage** — determine severity based on impact
3. **Contain** — activate kill switch, drain orders, isolate affected component
4. **Diagnose** — review audit trail, metrics snapshot, recent deployment
5. **Remediate** — rollback, restore from backup, patch
6. **Verify** — run preflight gate (`PreflightService.check()`) before resuming
7. **Post-mortem** — document timeline, root cause, preventive measures

### Kill Switch Activation

```bash
# Manual — record failures on kill switch to trip circuit breaker
# Then reset only after root cause is confirmed resolved
traderos risk reset   # if CLI available, else via API
```

### Recovery Steps After Incident

1. Run `PreflightService.check()` — verify audit chain, reconciliation, kill switch
2. For live mode, confirm `LIVE_TRADING_CONFIRMED=true`
3. Resume trading cycles

---

## Monitoring

### Key Metrics

| Metric | Description | Warning | Critical |
|--------|-------------|---------|----------|
| `circuit_breaker.tripped` | Kill-switch activations | > 0 | > 3 in 1h |
| `cycles.completed` | Completed trading cycles | < expected | 0 for 5 min |
| `cycle.duration_ms` | Per-cycle duration | > 1000ms | > 5000ms |
| `trades.executed` | Trade throughput | Anomaly | 0 for 10 min |

### Health Endpoints

```bash
# API health
curl http://localhost:8000/v1/health

# Prometheus metrics
curl http://localhost:8000/metrics
```

### Logging

Structured JSON logs via `setup_json_logging()`.  Key fields:
`timestamp`, `level`, `logger`, `message`, `event_type`, `market_id`, `strategy`

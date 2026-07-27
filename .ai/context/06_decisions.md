# 06 — Architecture Decisions

## Purpose
Complete index of every ADR, current architectural decisions, rejected alternatives, and pending decisions. Prevents repeated deliberation.

## Authority Level
**Foundational** — decisions are binding unless superseded by new ADR.

## Consumers
All AI agents, architects, technical leads.

## Dependencies
- `docs/adr/*.md` — each ADR file

## Source Documents
- ADR files in `docs/adr/`

## Update Rules
- Add new entry when ADR is ratified
- Update status when decision is superseded
- Reviewed monthly at architecture review

---

## Current ADRs

| ADR | Title | Status | Decision | Date |
|-----|-------|--------|----------|------|
| ADR-005 | SQLite Dev / PostgreSQL Prod | Accepted | SQLite for dev/test, PostgreSQL for prod. Migration via versioned migration framework. | Current |

## In Progress ADRs

| ADR | Title | Required By | Assigned WP |
|-----|-------|-------------|-------------|
| ADR-001 | Modular Monolith with Extraction Path | WP-008 | WP-008 |
| ADR-002 | Event Bus Selection | WP-016 | TBD |
| ADR-003 | Test Strategy | WP-002 | WP-002 |
| ADR-004 | Logging Standardization | WP-015 | TBD |

## Architectural Decisions

### Decisions Made

| Decision | Rationale | Source |
|----------|-----------|--------|
| **Monorepo, single package** | Simplifies dev, CI, deployment | [C:5] |
| **SQLite first** | Zero-config local development | ADR-005 |
| **CLI-first interface** | Enables scripting and automation | [C:5 Key Decision 3] |
| **src/ layout** | Standard Python packaging, prevents import confusion | WP-008 |
| **pytest over unittest** | Modern, faster, better fixtures | WP-002 |
| **ruff over flake8** | Unified linter+formatter, 10-100x faster | WP-003 |
| **pyright over mypy** | Better performance, strict mode native | WP-004 |
| **Knowledge graph in SQLite** | No external dependency, related data stays together | [C:6] |
| **Strict mode pyright** | Catches type errors at CI time, not runtime | WP-004 |
| **Re-export shims for legacy** | Enables phased migration without breaking imports | WP-008 |

### Decisions Rejected

| Rejected | Why | In Favor Of |
|----------|-----|-------------|
| **Multi-repo** | Coordination overhead outweights isolation benefits | Monorepo |
| **PostgreSQL from start** | Developer friction for local setup | SQLite-first |
| **mypy** | Slower, more false positives | pyright |
| **FastAPI for CLI** | REST API is secondary interface | CLI-first |
| **Microservices** | Premature distribution | Modular monolith |
| **Django ORM** | Heavy dependency for simple schema | Raw SQLite + Repository pattern |
| **Celery for async** | Overengineered for current scale | Simple thread pool + event bus |

### Decisions Pending

| Decision | Needed By | Blocked By |
|----------|-----------|------------|
| Event bus technology | WP-016 | ADR-002 |
| Logging framework | WP-015 | ADR-004 |
| REST API framework | WP-056 | — |
| Dashboard framework | WP-058 | — |

## Decision Process

1. Problem identified → drafted as ADR
2. ADR placed in `docs/adr/ADR-NNN.md`
3. Reviewed at architecture review
4. Ratified → status = Accepted
5. Implementation tracked via WP assignment

## References
- `docs/adr/` — all ADR files
- Master Execution Programme §16 — ADR Implementation Schedule
- Constitution §3 — Decision Rights

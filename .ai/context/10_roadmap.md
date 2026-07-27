# 10 — Roadmap

## Purpose
Current capability roadmap, milestone tracking, and work package status. Single source of truth for what is being worked on now and what comes next.

## Authority Level
**Informative** — reflects execution plan. Updated weekly.

## Consumers
AI agents (to determine context), PMs, engineers, stakeholders.

## Dependencies
- Master Execution Programme §30 (Final Timeline)
- `docs/sprints/SPRINT_3.md`

## Source Documents
- Master Execution Programme Sections 9, 10, 13, 30

## Update Rules
- Updated weekly at engineering sync
- Updated immediately when WP status changes

---

## Capability Roadmap

```
Phase 1: Foundation (Months 1-2)      ████████████████░░░░░░░░  COMPLETE
Phase 2: Architecture (Months 2-4)    ██░░░░░░░░░░░░░░░░░░░░░░  IN PROGRESS
Phase 3: Engine Services (Months 4-8) ░░░░░░░░░░░░░░░░░░░░░░░░  PENDING
Phase 4: Interfaces (Months 8-10)     ░░░░░░░░░░░░░░░░░░░░░░░░  PENDING
Phase 5: Release (Months 10-12)       ░░░░░░░░░░░░░░░░░░░░░░░░  PENDING
```

## Current Milestone

**Milestone**: Architecture Framework (Epic 1.2) — WP-009 to WP-017  
**Current Work Package**: WP-009 — Domain Entity Dataclasses  
**Target Completion**: Next sprint

## Current Work Package Status

| WP | Title | Status | Assigned | Dependencies |
|----|-------|--------|----------|-------------|
| WP-001 | Makefile & Tooling | ✅ Done | — | — |
| WP-002 | pytest Migration | ✅ Done | — | — |
| WP-003 | Linting | ✅ Done | — | — |
| WP-004 | Type Checking | ✅ Done | — | — |
| WP-005 | Docker | ✅ Done | — | — |
| WP-006 | CI Pipeline | ✅ Done | — | — |
| WP-007 | DB Migration Framework | ✅ Done | — | — |
| WP-008 | Package Restructure | ✅ Done | — | — |
| **WP-009** | **Domain Entity Dataclasses** | **⬆️ Next** | — | WP-008 |
| WP-010 | Repository Interfaces | 📋 Planned | — | WP-009 |
| WP-011 | InMemory Repositories | 📋 Planned | — | WP-010 |
| WP-012 | SQLite Repositories | 📋 Planned | — | WP-011 |
| WP-013 | Config v2 | 📋 Planned | — | WP-009 |
| WP-014 | Error Handling Framework | 📋 Planned | — | WP-009 |
| WP-016 | Event Bus | 📋 Planned | — | WP-014 |

## Completed Work

All WP-001 through WP-008 are complete. See CHANGELOG.md for details.

## Blocked Work

| Item | Blocked By | Since | Unblock Plan |
|------|-----------|-------|-------------|
| PostgreSQL migrations | ADR-005 decision | Start | ADR-005 ratified, deferred to WP-012+ |

## Critical Path

```
Now → WP-009 (Entities) → WP-010 (Repositories) → WP-011 (InMemory) 
   → WP-012 (SQLite Repos) → WP-013 (Config v2) → WP-014 (Errors)
   → WP-016 (Event Bus) → WP-017 (Architecture Tests)
   ↓
WP-018 → WP-025 → WP-032 → WP-039 → WP-048 (Engine Services)
```

**Current bottleneck**: WP-009 / WP-010 — domain entity definitions must precede all service implementations.

## References
- Master Execution Programme §30 — Final 12-Month Timeline
- Master Execution Programme §9 — Milestones
- Master Execution Programme §13 — Critical Path Analysis
- `docs/sprints/SPRINT_3.md` — current sprint

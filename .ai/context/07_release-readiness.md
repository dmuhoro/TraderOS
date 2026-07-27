# 07 — Release Readiness

## Purpose
Definition of Ready (DoR), Definition of Done (DoD), quality gates, and release criteria for TraderOS. Every work package and PR is evaluated against these criteria.

## Authority Level
**Enforceable** — gates block progression through pipeline.

## Consumers
AI agents, engineers, PMs, release managers.

## Dependencies
- Master Execution Programme §20 (DoR), §21 (DoD)
- `.ai/context/04_code-standards.md` — review checklist

## Source Documents
- Master Execution Programme Sections 20, 21, 24, 18

## Update Rules
- Reviewed at end of each milestone
- Updated when quality gaps are identified

---

## Definition of Ready (DoR)

Before work begins on any WP, story, or task:

- [ ] Acceptance criteria written and reviewed
- [ ] Dependencies identified and available
- [ ] Test strategy defined (unit, integration, manual)
- [ ] ADR created if architectural impact
- [ ] Effort estimate agreed
- [ ] No blocking external dependencies unaddressed
- [ ] Constitution compliance verified
- [ ] AI context files consulted for consistency

## Definition of Done (DoD)

A WP is **Done** only when ALL of:

- [ ] Code implemented per `.ai/context/04_code-standards.md`
- [ ] `make ci` passes (ruff, black, isort, pyright, pytest)
- [ ] Coverage meets or exceeds 30% threshold
- [ ] No new pyright errors introduced
- [ ] Tests added for new functionality
- [ ] CHANGELOG.md updated
- [ ] SPRINT_N.md updated
- [ ] ADRs created/updated if applicable
- [ ] `.ai/context/` files updated if architecture changed
- [ ] Code reviewed and approved

## Quality Gates

| Gate | Stage | Enforced By | Pass Criteria |
|------|-------|-------------|--------------|
| G1 | Pre-commit | pre-commit hooks | trailing-whitespace, black, ruff |
| G2 | Push | CI lint job | ruff, black --check, isort --check |
| G3 | PR | CI typecheck | pyright 0 errors |
| G4 | PR | CI test | pytest 7/7 pass, coverage ≥ 30% |
| G5 | PR | CI docker | docker compose build succeeds |
| G6 | Merge | Branch protection | G1–G5 pass, 1+ approval |
| G7 | Release | Release pipeline | All checklists complete |

## Portfolio Readiness Checklist (v1.0)

- [ ] All 15+ tables documented and migrated
- [ ] Knowledge graph workflow complete (Obs→Hyp→Test→Result→Lesson)
- [ ] Backtesting engine validated against historical data
- [ ] Risk engine enforces all limits
- [ ] Paper trading executes without manual intervention
- [ ] CLI interfaces cover 80%+ of common operations
- [ ] Dashboard visualizes key metrics
- [ ] CI/CD pipeline green for 10+ consecutive runs
- [ ] Pyright strict mode: 0 errors
- [ ] Test coverage ≥ 50%
- [ ] Documentation covers installation, configuration, usage
- [ ] Docker image builds and runs
- [ ] ADR register complete (all architectural decisions recorded)

## Production Readiness Checklist

- [ ] PostgreSQL adapter implemented and tested
- [ ] Secrets management (not .env in production)
- [ ] Structured logging to stdout (container-friendly)
- [ ] Health check endpoint
- [ ] Graceful shutdown on SIGTERM
- [ ] Rate limiting on external API calls
- [ ] Retry with backoff on transient failures
- [ ] Metrics exported (Prometheus format)
- [ ] Run manifest captured per execution

## Evidence Requirements

Each quality gate produces artifacts:
- **G1–G2**: CI logs (auto-published)
- **G3**: Pyright report (CI artifact)
- **G4**: Coverage report (HTML in CI artifacts)
- **G5**: Docker image digest
- **G6**: PR approval audit trail
- **G7**: Release tag, CHANGELOG, migration scripts

## References
- Master Execution Programme §18 — Quality Gates
- Master Execution Programme §20 — Definition of Ready
- Master Execution Programme §21 — Definition of Done
- Master Execution Programme §24 — Portfolio Readiness Checklist
- `.ai/context/04_code-standards.md` — code quality baseline

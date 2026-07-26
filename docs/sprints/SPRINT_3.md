# Sprint 3 — Programme Alpha: Engineering Foundations

Programme Reference: Master Execution Programme — Workstream 1 (Foundation)
Version Target: v0.3.0

## Objective

Establish the development toolchain, CI/CD pipeline, containerization, test infrastructure, and namespace package structure required for all future engineering work. Enforce standards mechanically through pre-commit hooks and CI.

## Work Packages

### WP-001: Makefile & Developer Tooling Setup — COMPLETED
- Makefile with standard targets (setup, test, lint, format, typecheck, clean, ci)
- pyproject.toml with unified tool configuration
- .pre-commit-config.yaml with automated hooks
- conftest.py for pytest session management
- All developer tools installed (ruff, black, isort, pytest, pyright, pre-commit)
- Codebase auto-formatted and partially linted

### WP-002: pytest Migration & Configuration — COMPLETED
- All 7 tests pass under pytest with coverage (40%+)
- Coverage configured with 30% fail-under threshold
- conftest.py provides test DB isolation via env vars

### WP-003: Linting & Code Quality Enforcement — COMPLETED
- 39 ruff errors fixed manually across the codebase
- `make lint` passes with zero errors

### WP-004: Type Checking — COMPLETED
- pyright strict mode configured
- 47 type errors resolved
- `make typecheck` passes with zero errors

### WP-005: Docker Containerization — COMPLETED
- Dockerfile (multi-stage Python 3.11-slim)
- docker-compose.yml with data/exports volumes
- .dockerignore

### WP-006: GitHub Actions CI — COMPLETED
- CI workflow: lint → typecheck → test → docker
- Coverage artifact upload

### WP-007: Database Migration Framework — COMPLETED
- Migration engine with version tracking
- Initial schema migration (v001)
- ADR-005 (SQLite Dev / PostgreSQL Prod)

### WP-008: Namespace Package Restructuring — COMPLETED
- New `src/traderos/` layered architecture:
  - `domain/` — analysis, liquidity, risk, strategies, backtesting, research
  - `infrastructure/` — config, database, data
  - `application/` — orchestrator
  - `interfaces/` — cli, visualization
- Old flat modules become re-export shims (dual directory structure)
- All internal imports updated to `traderos.xxx.yyy` paths
- Tooling configs updated (pyproject.toml, Makefile, Docker, CI)

## Deliverables
- Development environment that "just works" (make setup → make test)
- CI/CD pipeline enforcing code quality
- Containerized development and deployment
- Database migration framework
- Namespace package structure aligned with target architecture

## Out of Scope
- Architecture layer decoupling (interfaces extraction — WP-009+)
- Feature development

## Success Criteria
- `make setup` configures complete environment in < 5 minutes
- `make test` passes with 7/7 tests in < 30 seconds
- `make ci` passes (lint + typecheck + test) with zero errors
- Pre-commit hooks block violations before commit
- CI pipeline green on every PR
- Docker development environment operational
- `import traderos.domain.XXX` works from any context

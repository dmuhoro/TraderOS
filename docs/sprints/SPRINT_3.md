# Sprint 3 — Programme Alpha: Engineering Foundations

Programme Reference: Master Execution Programme — Workstream 1 (Foundation)
Version Target: v0.3.0

## Objective

Establish the development toolchain, CI/CD pipeline, containerization, and test infrastructure required for all future engineering work. Enforce standards mechanically through pre-commit hooks and CI.

## Work Packages

### WP-001: Makefile & Developer Tooling Setup — COMPLETED
- Makefile with standard targets (setup, test, lint, format, typecheck, clean, ci)
- pyproject.toml with unified tool configuration
- .pre-commit-config.yaml with automated hooks
- conftest.py for pytest session management
- All developer tools installed (ruff, black, isort, pytest, pyright, pre-commit)
- Codebase auto-formatted and partially linted

### WP-002: pytest Migration & Configuration — IN PROGRESS
- Ensure all existing tests run under pytest (7/7 passing)
- Add test coverage reporting
- Configure test isolation

### WP-003: Linting & Formatting Configuration — PENDING
- Fix remaining 43 lint errors across codebase
- Ensure `make lint` passes with zero errors

### WP-004: Type Checking Configuration — PENDING
- Configure pyright strict mode
- Fix type annotation issues across codebase
- Ensure `make typecheck` passes with zero errors

### WP-005: Dockerfile & docker-compose.yml — PENDING
- Dockerfile for production image
- docker-compose.yml for development environment
- .dockerignore

### WP-006: GitHub Actions CI Pipeline — PENDING
- CI workflow: lint → typecheck → test on every push
- Branch protection rules

### WP-007: SQLite → PostgreSQL Migration Framework — PENDING
- Schema versioning
- Migration scripts
- Repository pattern verification

## Deliverables
- Development environment that "just works" (make setup → make test)
- CI/CD pipeline enforcing code quality
- Containerized development and deployment
- Database migration framework

## Out of Scope
- Architecture restructuring (Workstream 2)
- Feature development

## Success Criteria
- `make setup` configures complete environment in < 5 minutes
- `make test` passes with 7/7 tests in < 30 seconds
- `make ci` passes (lint + typecheck + test) with zero errors
- Pre-commit hooks block violations before commit
- CI pipeline green on every PR
- Docker development environment operational

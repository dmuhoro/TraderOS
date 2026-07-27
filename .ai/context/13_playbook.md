# 13 — AI Agent Playbook

## Purpose
Quick-start guide for AI agents entering the repository. How to analyse, plan, implement, review, verify, and communicate effectively within TraderOS.

## Authority Level
**Operational** — guidance for AI agents. Not enforceable, but following it produces better results.

## Consumers
AI agents (all models), new engineers onboarding.

## Dependencies
- All `.ai/context/*.md` files
- `docs/engineering/CONSTITUTION.md`
- `docs/engineering/MASTER_EXECUTION_PROGRAMME.md`

## Source Documents
- This playbook distills patterns from all context files
- Constitution §1 (Executive Vision)
- Master Execution Programme §1 (Programme Mandate)

## Update Rules
- Updated when workflows or standards change
- Updated when common failure patterns are identified

---

## How to Analyse

1. **Read the Constitution first** — understand the mission, vision, and principles. The Constitution is the supreme authority.
2. **Check the Programme** — what WP are we executing? What phase? What milestone?
3. **Load relevant context files** — for architecture: `01_architecture.md`. For standards: `04_code-standards.md`. For the roadmap: `10_roadmap.md`.
4. **Inspect the system map** (`02_system-map.md`) — understand where things live.
5. **Read existing code** — understand conventions by reading neighbor files.
6. **Check decisions** (`06_decisions.md`) — avoid reopening settled debates.

## How to Plan

1. **Identify the target WP** from the Master Execution Programme
2. **Check dependencies** — what WPs must be done first? (check `10_roadmap.md`)
3. **Break down** — what files need to change? What new files are needed?
4. **Estimate scope** — how many files? What's the risk?
5. **Draft approach** — sequence of operations, one step at a time
6. **Review plan** — against Constitution principles, architecture, standards
7. **Get feedback** — present plan to human reviewer before coding

## How to Implement

1. **Follow the standards** in `04_code-standards.md` — types, naming, formatting
2. **Respect the architecture** in `01_architecture.md` — dependency rules, layer boundaries
3. **Use the domain model** in `03_domain-model.md` — entities, relationships, invariants
4. **Follow db contracts** in `05_db-contracts.md` — if touching persistence
5. **Check security** in `08_security.md` — if touching external boundaries
6. **One file at a time** — implement, save, verify
7. **Add tests** — test the new functionality
8. **Run `make ci`** before committing

## How to Review

1. **Architecture**: Does this violate layer rules? (check `01_architecture.md`)
2. **Standards**: Types correct? Naming right? F-strings in logging? (check `04_code-standards.md`)
3. **Domain**: Entities match domain model? Invariants preserved? (check `03_domain-model.md`)
4. **Security**: Secrets exposed? SQL injection possible? (check `08_security.md`)
5. **Tests**: Coverage adequate? Edge cases handled?
6. **Consistency**: Follows existing patterns in neighbor files?

## How to Verify

1. `make lint` — zero ruff errors
2. `make format-check` — black + isort pass
3. `make typecheck` — zero pyright errors
4. `make test` — all tests pass, coverage ≥ 30%
5. `make ci` — everything above + docker build
6. Manual verification: run the relevant CLI command
7. Edge cases: empty data, missing config, network timeout

## How to Communicate

1. **Be concise** — one line answers when possible. Elaborate only if asked.
2. **Reference sources** — `[C:4]`, `WP-009`, `ADR-005`, `.ai/context/01_architecture.md`
3. **State assumptions** — "Assuming WP-009 is complete..."
4. **Flag uncertainties** — "Not sure about the entity lifecycle here. Check [C:6]."
5. **Report progress** — what was done, what's next, what's blocked
6. **Use the project terminology** — "domain", "infrastructure", "knowledge graph", "research chain"

## What AI Agents Must Never Do

1. NEVER commit secrets, keys, or credentials
2. NEVER use f-strings in logger calls
3. NEVER concatenate strings for SQL queries
4. NEVER import infrastructure in domain code
5. NEVER delete files without checking cross-references
6. NEVER change an ADR without a new ADR
7. NEVER bypass CI gates
8. NEVER modify the Constitution or Master Execution Programme without explicit instruction
9. NEVER assume unreleased external APIs exist (always check installed packages)
10. NEVER generate URLs or documentation paths that don't exist — verify first

## Example Workflows

### Adding a new indicator
```
1. Read: 01_architecture.md (where does it go?)
2. Read: 03_domain-model.md (Indicator entity)
3. Read: 04_code-standards.md (types, naming)
4. Read existing: domain/analysis/indicators.py (conventions)
5. Implement: add method to MarketAnalyzer
6. Verify: make ci
7. Update: CHANGELOG.md
```

### Implementing a new WP
```
1. Read: 10_roadmap.md (what's the next WP?)
2. Read: Master Execution Programme for WP definition
3. Read: Relevant context files
4. Plan: sequence of file changes
5. Implement: one file at a time
6. Verify: make ci after each logical change
7. Update: SPRINT_N.md, CHANGELOG.md, 10_roadmap.md
8. Push: commit and push to feature branch
```

## References
- All `.ai/context/*.md` files
- `.ai/agents/*.md` — specialised agent manuals
- `docs/engineering/CONSTITUTION.md` — supreme authority
- `docs/engineering/MASTER_EXECUTION_PROGRAMME.md` — execution authority

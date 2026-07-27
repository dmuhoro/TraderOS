# 11 — Workflow Rules

## Purpose
Engineering workflow — branch strategy, PR rules, ADR process, sprint cadence, release workflow. Every AI agent must follow these workflows.

## Authority Level
**Enforceable** — deviations require CTO approval.

## Consumers
AI agents, all engineers, reviewers, CI/CD.

## Dependencies
- Constitution §3 (Decision Rights), §9 (Engineering Workflow)
- Master Execution Programme §19, §22, §23, §27

## Source Documents
- Master Execution Programme Sections 19, 22, 23, 27, 28

## Update Rules
- Reviewed at quarterly retrospective
- Updated when workflow improves

---

## Branch Strategy

```
main                    Production-ready, protected
  └── develop           Integration branch, protected
       ├── feature/*    New capabilities
       ├── fix/*        Bug fixes
       ├── wp/*         Work package branches
       └── release/*    Release candidates
```

- `main`: Production. Only merge from `develop` or `release/*`.
- `develop`: Integration. Feature branches merge here.
- `feature/*`: Branch from `develop`. Merge back via PR.
- `fix/*`: Branch from `develop` or `main` for hotfixes.
- `wp/*`: Work package branches for complex multi-PR work.
- `release/*`: Branch from `develop` for release prep.

## PR Rules

1. Title: `type: brief description` (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`)
2. Description: What, Why, How, Testing evidence
3. Link to WP number in description
4. At least 1 approval required
5. All CI gates must pass (G1–G5)
6. No direct pushes to `main` or `develop`
7. Squash merge recommended for feature branches

## ADR Workflow

1. **Propose**: Draft ADR in `docs/adr/ADR-NNN.md`
2. **Review**: Present at architecture review
3. **Decide**: Ratify, reject, or defer
4. **Record**: Update `.ai/context/06_decisions.md`
5. **Implement**: Create WP to implement decision
6. **Verify**: Tests confirm decision is correctly implemented

## Sprint Workflow

1. **Planning**: Select WPs from Master Execution Programme
2. **Execution**: Work sequentially through assigned WPs
3. **Review**: Demo completed WPs at sprint end
4. **Retro**: What worked, what didn't, what to improve
5. **Update**: Update `docs/sprints/SPRINT_N.md` with progress

Current sprint format: docs/sprints/SPRINT_3.md

## Review Workflow

1. Author opens PR
2. CI runs G1–G5 gates
3. Reviewer checks:
   - Architecture compliance (`.ai/context/01_architecture.md`)
   - Code standards (`.ai/context/04_code-standards.md`)
   - Test coverage
   - No security violations (`.ai/context/08_security.md`)
4. Reviewer approves or requests changes
5. Author addresses feedback
6. Merge when all gates pass and approved

## Release Workflow

1. `develop` reaches release criteria (`.ai/context/07_release-readiness.md`)
2. Create `release/vX.Y.Z` branch
3. Run full release checklist
4. Tag with semver: `v1.0.0`
5. Merge to `main` and `develop`
6. Build and push Docker image
7. Update CHANGELOG.md

## AI Workflow

When an AI agent operates in this repository:

1. **Load context**: Read `.ai/context/*.md` files relevant to task
2. **Consult Constitution**: Check `docs/engineering/CONSTITUTION.md` for binding rules
3. **Check Programme**: Verify alignment with Master Execution Programme
4. **Plan**: Draft implementation plan referencing specific WP
5. **Implement**: Write code following `.ai/context/04_code-standards.md`
6. **Verify**: Run `make ci` before committing
7. **Document**: Update CHANGELOG, sprint docs, `.ai/context/` if architecture changed
8. **PR**: Create PR with description following PR rules

## References
- [C:3] Decision Rights — who decides what
- [C:9] Engineering Workflow — full workflow specification
- Master Execution Programme §27 — Weekly Engineering Operating Rhythm
- Master Execution Programme §28 — Monthly Architecture Review Process
- `.ai/context/07_release-readiness.md` — quality gates and checklists

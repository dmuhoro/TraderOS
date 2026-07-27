# Maintenance Guide

## Purpose
How to maintain the AI Engineering Operating System. Update cadence, ownership, review process, and deprecation strategy for every file.

## Update Cadence

| File | Update Trigger | Owner | Review Cadence |
|------|---------------|-------|----------------|
| `01_architecture.md` | New layer, module, or dependency rule change | CTO | Monthly |
| `02_system-map.md` | File add/move/delete | Tech Lead | Monthly |
| `03_domain-model.md` | New entity, relationship, or invariant change | Domain Lead | Per entity addition |
| `04_code-standards.md` | Tool config change, new pattern | Tech Lead | Quarterly |
| `05_db-contracts.md` | Schema change, new migration | DB Lead | Per migration |
| `06_decisions.md` | ADR ratified, superseded, or rejected | CTO | Monthly |
| `07_release-readiness.md` | Quality gate change, process improvement | Release Manager | Per release |
| `08_security.md` | Vulnerability, threat model change | Security Lead | Quarterly |
| `09_security-subsystems.md` | New subsystem, new attack surface | Security Lead | Per new module |
| `10_roadmap.md` | WP status change, milestone completion | PM | Weekly |
| `11_workflow-rules.md` | Workflow improvement | CTO | Quarterly |
| `12_ui-context.md` | Design system change | Design Lead | Per UI milestone |
| `13_playbook.md` | AI agent failure pattern identified | CTO | Quarterly |
| Agent files | Agent role change, new capability | CTO | Quarterly |
| Cross-ref matrix | Context file add/remove | CTO | Quarterly |
| Dependency graph | Context file add/remove | CTO | Quarterly |
| Maintenance guide | Process change | CTO | Annually |
| Expansion strategy | Phase transition | CTO | Quarterly |

## Ownership

| Role | Owns | Approves Changes To |
|------|------|-------------------|
| CTO | 01, 06, 10, 11, 13, all agent files, meta files | Everything |
| Tech Lead | 02, 04 | 01, 03, 05 |
| Domain Lead | 03 | 01 |
| DB Lead | 05 | 03 |
| Security Lead | 08, 09 | 04 (security sections) |
| Release Manager | 07 | — |
| Design Lead | 12 | — |
| PM | 10 (status only) | — |

## Review Process

### Monthly Architecture Review
- Review: 01, 03, 05, 06
- Attendees: CTO, Tech Lead, Domain Lead
- Output: Change requests or approvals

### Quarterly Technical Debt Review
- Review: 04, 08, 09, 11, agent files
- Attendees: All leads
- Output: Improvement backlog items

### Per Release
- Review: 07, 10
- Attendees: Release Manager, CTO
- Output: Release sign-off

## Deprecation Strategy

If a context file becomes obsolete:
1. Mark with `> **Status**: Deprecated — replaced by {new-file}` header
2. Keep file for 3 months (grace period for AI agents)
3. Remove after 3 months or after all agents have been updated
4. Update cross-reference matrix and dependency graph

## Versioning

The AI Engineering Operating System is versioned alongside TraderOS:
- Minor version bump: context file update
- Major version bump: restructuring or new file category

Version tracked in `.ai/VERSION`

## Quality Assurance

Every update to `.ai/context/` files must:
1. Pass link checking (all references to other files are valid)
2. Update cross-reference matrix if references changed
3. Update dependency graph if topology changed
4. Notify downstream consumers (agents that depend on changed files)

## References
- `.ai/context/99_cross_reference_matrix.md` — for impact analysis before changes
- `.ai/context/99_dependency_graph.md` — for dependency topology

# Cross-Reference Matrix

## Purpose
Maps every context file to every other context file, source document, and external reference. Enables AI agents to quickly find related information. No concept exists in isolation.

## Usage
- Find which files reference a concept
- Verify coverage (no orphaned concepts)
- Trace dependencies between documents

---

## File-to-File Dependency Matrix

| File | Depends On | Referenced By |
|------|-----------|---------------|
| `01_architecture.md` | Constitution [C:4, C:5], MEP §3, §7 | 02, 04, 05, 10, 11, 12, 13 |
| `02_system-map.md` | 01_architecture.md | 10, 13 |
| `03_domain-model.md` | Constitution [C:6] | 05, 10, 11 |
| `04_code-standards.md` | 01_architecture.md, pyproject.toml | 07, 11, 13 |
| `05_db-contracts.md` | 03_domain-model.md, ADR-005 | 09, 11 |
| `06_decisions.md` | All ADRs, MEP §16 | All agents |
| `07_release-readiness.md` | MEP §18, §20, §21, §24 | release-agent, reviewer-agent |
| `08_security.md` | Constitution [C:4.4] | 09, security-agent |
| `09_security-subsystems.md` | 08_security.md | security-agent, auditor-agent |
| `10_roadmap.md` | MEP §30, SPRINT_3.md | All agents, planner-agent |
| `11_workflow-rules.md` | Constitution [C:3, C:9], MEP §19, §22, §23, §27 | All agents |
| `12_ui-context.md` | Constitution [C:5], 01_architecture.md | product-agent |
| `13_playbook.md` | All context files | All AI agents |

## Agent-to-Context Dependency Matrix

| Agent | Required Context Files |
|-------|----------------------|
| planner-agent | 01, 02, 03, 10, 13, MEP |
| builder-agent | 01, 02, 03, 04, 05, 08 |
| auditor-agent | 01, 02, 03, 04, 05, 08, Constitution |
| reviewer-agent | 01, 03, 04, 05, 07, 08 |
| migration-agent | 03, 05, ADR-005 |
| performance-agent | 02, 05 |
| security-agent | 08, 09, 04 |
| product-agent | 01, 03, 10, MEP |
| release-agent | 07, 10, 02, MEP §23 |

## Concept-to-File Map

| Concept | Primary File | Also In |
|---------|-------------|---------|
| Architecture layers | 01_architecture.md | Constitution [C:4, C:5] |
| Module boundaries | 01_architecture.md | 02_system-map.md |
| Dependency rules | 01_architecture.md | 04_code-standards.md |
| Repository structure | 02_system-map.md | — |
| Domain entities | 03_domain-model.md | Constitution [C:6] |
| Code standards | 04_code-standards.md | pyproject.toml |
| Database schema | 05_db-contracts.md | migration files |
| Migration rules | 05_db-contracts.md | 03_domain-model.md |
| ADR index | 06_decisions.md | docs/adr/*.md |
| Quality gates | 07_release-readiness.md | MEP §18 |
| Security | 08_security.md | 09_security-subsystems.md |
| Roadmap | 10_roadmap.md | MEP §30 |
| Workflow | 11_workflow-rules.md | Constitution [C:9] |
| UI design | 12_ui-context.md | — |
| Agent playbook | 13_playbook.md | All context files |

## External Source Dependencies

| External Source | Referenced By |
|----------------|--------------|
| Constitution [C:1] | 13_playbook.md |
| Constitution [C:3] | 11_workflow-rules.md |
| Constitution [C:4] | 01_architecture.md, 08_security.md |
| Constitution [C:5] | 01_architecture.md, 12_ui-context.md |
| Constitution [C:6] | 03_domain-model.md |
| Constitution [C:8.18] | 05_db-contracts.md |
| Constitution [C:9] | 11_workflow-rules.md |
| Constitution [C:10] | 04_code-standards.md |
| MEP §1 | 13_playbook.md |
| MEP §3 | 01_architecture.md |
| MEP §7 | 01_architecture.md |
| MEP §9 | 10_roadmap.md |
| MEP §13 | 10_roadmap.md |
| MEP §15 | 08_security.md |
| MEP §16 | 06_decisions.md |
| MEP §18 | 07_release-readiness.md |
| MEP §19 | 11_workflow-rules.md |
| MEP §20 | 07_release-readiness.md |
| MEP §21 | 07_release-readiness.md |
| MEP §22 | 11_workflow-rules.md |
| MEP §23 | 11_workflow-rules.md, release-agent |
| MEP §24 | 07_release-readiness.md |
| MEP §27 | 11_workflow-rules.md |
| MEP §30 | 10_roadmap.md |
| ADR-005 | 05_db-contracts.md, 06_decisions.md |
| WP-008 | 01_architecture.md |
| WP-009 | 01_architecture.md, 03_domain-model.md |
| WP-010 | 05_db-contracts.md |

This matrix is verified during quarterly architecture reviews.

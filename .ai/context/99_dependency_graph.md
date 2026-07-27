# Dependency Graph

## Purpose
Complete dependency graph for the AI Engineering Operating System. Shows how every context file, agent manual, and external source connects. Enables AI agents to understand the information topology.

## Legend
- `→` = depends on (reads/references)
- `⇄` = bidirectional dependency
- `--` = references (informational)

---

## Context File Dependency Graph

```
Constitution ──→ 01_architecture.md ──→ 02_system-map.md
     │                                    │
     ├──→ 03_domain-model.md              │
     │         │                          │
     │         └──→ 05_db-contracts.md    │
     │                                    │
     ├──→ 04_code-standards.md ←── pyproject.toml
     │         │
     │         └──→ 07_release-readiness.md ←── MEP §18,§20,§21,§24
     │
     ├──→ 08_security.md ──→ 09_security-subsystems.md
     │
     ├──→ 11_workflow-rules.md ←── MEP §19,§22,§23,§27
     │
     └──→ 12_ui-context.md ←── 01_architecture.md

ADR-005 ──→ 05_db-contracts.md
          ──→ 06_decisions.md

MEP ──→ 10_roadmap.md ──→ SPRINT_3.md
  │         │
  ├──→ 06_decisions.md
  ├──→ 07_release-readiness.md
  └──→ 11_workflow-rules.md

13_playbook.md ←── ALL context files
```

## Agent Dependency Graph

```
planner-agent ←── 01, 02, 03, 10, 13, MEP
     │
     └──→ builder-agent ←── 01, 02, 03, 04, 05, 08
              │
              ├──→ reviewer-agent ←── 01, 03, 04, 05, 07, 08
              │
              ├──→ migration-agent ←── 03, 05, ADR-005
              │
              └──→ performance-agent ←── 02, 05

auditor-agent ←── 01, 02, 03, 04, 05, 08, Constitution

security-agent ←── 08, 09, 04

product-agent ←── 01, 03, 10, MEP
     │
     └──→ release-agent ←── 07, 10, 02, MEP §23
```

## Full Dependency Network (Topological Order)

```
Level 0 (No deps):   Constitution, MEP, ADR-005, pyproject.toml
Level 1 (External):  01_architecture.md, 03_domain-model.md, 08_security.md
Level 2 (Context):   02_system-map.md, 04_code-standards.md, 05_db-contracts.md
                     06_decisions.md, 09_security-subsystems.md, 12_ui-context.md
Level 3 (Executable): 07_release-readiness.md, 10_roadmap.md, 11_workflow-rules.md
Level 4 (Meta):      13_playbook.md, cross-reference matrix, dependency graph
Level 5 (Agent):      All 9 agent files
```

## Circular Dependency Check

```
No circular dependencies found. All paths are acyclic.

Verification:
  Constitution → 01 → 02 → ... (all forward)
  MEP → 10 → all agents (no cycles)
  All agent files only read context, never write back
```

## Critical Path

The most heavily referenced files (highest betweenness centrality):

1. **01_architecture.md** — referenced by 8 other context files + 5 agents
2. **04_code-standards.md** — referenced by 3 context files + 2 agents
3. **03_domain-model.md** — referenced by 3 context files + 4 agents
4. **10_roadmap.md** — referenced by all 9 agents
5. **07_release-readiness.md** — critical for releases

These files must be maintained with the highest care. Any change to them must be reviewed against all downstream consumers.

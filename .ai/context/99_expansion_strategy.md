# Future Expansion Strategy

## Purpose
How the AI Engineering Operating System evolves with TraderOS. Design principles for adding new context files, agents, and capabilities without creating duplication or inconsistency.

## Design Principles

### 1. One Concept, One File
Every concept exists in exactly one authoritative file. If a concept appears in multiple files, either:
- One file is the primary, others reference it, OR
- The concept needs its own file

### 2. References, Not Copies
Never duplicate knowledge across files. Use file references (e.g., "See `03_domain-model.md` for entity definitions") instead of copying content.

### 3. File Count Scales with Complexity
As TraderOS grows, context files will be added. The structure should remain navigable:
- 01-19: Core context (permanent)
- 20-49: Domain-specific context (added per new domain)
- 50-79: Infrastructure-specific context
- 80-99: Meta-files (cross-reference, dependency, maintenance)

### 4. Agent Files Scale with Roles
New agent files are created when:
- A distinct engineering role emerges
- An existing agent's responsibilities exceed single-file scope
- A new AI model requires specialized prompting

## Adding a New Context File

1. **Identify gap**: What concept isn't covered by existing files?
2. **Check uniqueness**: Search all files for the concept. If it exists, update that file instead.
3. **Assign number**: Follow numbering scheme (01-19 core, 20-49 domain, etc.)
4. **Write file**: Follow template (Purpose, Authority Level, Consumers, Dependencies, Source Documents, Update Rules, Contents, References)
5. **Update meta-files**:
   - `99_cross_reference_matrix.md` — add entries
   - `99_dependency_graph.md` — add nodes and edges
   - `99_maintenance_guide.md` — add update cadence
6. **Update agents**: Add to required context files where applicable

## Adding a New Agent

1. **Define mission**: What specific gap does this agent fill?
2. **Boundary check**: Does this overlap with an existing agent?
3. **Write agent file**: Follow template (Mission, Responsibilities, Inputs, Outputs, etc.)
4. **Update meta-files**: Add to cross-reference matrix

## Phased Expansion Plan

### Phase 1 (Current): Engineering Foundation
- 13 context files
- 9 agent files
- Covers: architecture, standards, domain, security, roadmap

### Phase 2 (After WP-017): Architecture Complete
- Add: domain-specific context files for market data, analysis, liquidity
- Update: 03_domain-model.md with full entity details
- Update: agents with domain-specific knowledge

### Phase 3 (After WP-054): Engine Services Complete
- Add: service-specific context files
- Add: service-specific agents if needed
- Update: 02_system-map.md with complete module coverage

### Phase 4 (After WP-063): Interfaces Complete
- Add: 12_ui-context.md finalized
- Add: frontend-specific agents
- Update: 01_architecture.md with API contract details

### Phase 5 (v1.0 Release): Stabilization
- Consolidate: merge thin context files into broader ones if beneficial
- Archive: remove deprecated context files after grace period
- Freeze: lock core context files for v1.x maintenance

## Anti-patterns

| Anti-pattern | Why | Instead |
|-------------|-----|---------|
| Copying Constitution sections into context files | Fragmentation | Reference by [C:N] |
| Agent files with overlapping responsibilities | Confusion | Merge or clearly boundary |
| Context files exceeding 200 lines | Hard to consume | Split by concept |
| Orphaned context files (no references) | Dead knowledge | Remove or cross-reference |
| Stale roadmap | Misleads agents | Update weekly |

## Numbering Convention

```
01-19: Core architectural context
20-29: Domain-specific analysis context
30-39: Research & knowledge graph context
40-49: Strategy & execution context
50-59: Infrastructure context
60-69: Interface & UI context
70-79: Operations & observability context
80-89: Release & quality context
90-99: Meta-files (cross-reference, dependency, maintenance, expansion)
```

## References
- `.ai/context/99_cross_reference_matrix.md` — current coverage
- `.ai/context/99_maintenance_guide.md` — update procedures
- `.ai/context/13_playbook.md` — AI agent methodology

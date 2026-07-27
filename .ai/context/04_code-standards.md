# 04 — Code Standards

## Purpose
Authoritative Python and architecture standards for TraderOS. Every AI agent must apply these standards when generating code.

## Authority Level
**Enforceable** — violations block PRs via CI.

## Consumers
AI agents, engineers, code reviewers, CI pipelines.

## Dependencies
- `.ai/context/01_architecture.md` — dependency rules
- `pyproject.toml` — tool configuration

## Source Documents
- Constitution §2 (Core Principles)
- Constitution §10 (Engineering Standards)
- Master Execution Programme §19 (Code Review Workflow)

## Update Rules
- Update when tool configuration changes
- Update when new patterns are adopted
- Reviewed quarterly at technical debt review

---

## Python Conventions

**Version**: Python 3.11+  
**Formatter**: Black (line-length 100)  
**Import Sorter**: isort (Black-compatible profile, force-single-line)  
**Linter**: ruff (extend-select: B, N, W, E, F, UP, LOG, G)  
**Type Checker**: pyright (strict mode)  
**Test Runner**: pytest + pytest-cov (threshold 30%)

## Architecture Conventions

1. **Module → File**: One class per file unless tightly coupled
2. **File → Line Limit**: 500 lines max. Split at 400.
3. **Package → __init__.py**: Re-export public API only
4. **Dependencies**: See `.ai/context/01_architecture.md` §Dependency Rules

## Naming

| Element | Convention | Example |
|---------|-----------|---------|
| Packages | `snake_case` | `traderos.domain.analysis` |
| Modules | `snake_case` | `indicators.py` |
| Classes | `PascalCase` | `MarketAnalyzer` |
| Functions | `snake_case` | `detect_regime()` |
| Variables | `snake_case` | `current_regime` |
| Constants | `UPPER_SNAKE` | `MAX_WINDOW_SIZE` |
| Private | leading underscore | `_filter_zones()` |
| Type vars | short Pascal | `T`, `TEntity` |

## Typing

- All function signatures MUST have type annotations
- Use `|` for unions (PEP 604), not `Optional[]`
- Use `list[X]` not `List[X]`
- Use `dict[K, V]` not `Dict[K, V]`
- Avoid `Any` — prefer `Unknown` or `TypeVar`
- Return types: always explicit, never omitted
- `None` returns: annotate `-> None`

## Error Handling

- Use domain-specific exceptions, not bare `Exception`
- Prefer early returns over nested try/except
- Log exceptions at point of capture, not re-raise wrapper
- External service errors: retry with backoff, then fail closed
- Validation errors: raise `ValueError` with descriptive message

```python
# GOOD
def calculate_position_size(capital: float, volatility: float) -> float:
    if volatility <= 0:
        raise ValueError(f"Volatility must be positive, got {volatility}")
    return (capital * RISK_FACTOR) / volatility

# BAD
def calc(c, v):
    try:
        return c * 0.01 / v
    except:
        return 0
```

## Logging

- Use `%s` formatting with logger, NOT f-strings
- `logger.info("Processing %s for %s", action, symbol)`
- NEVER `logger.info(f"Processing {action} for {symbol}")`
- Log levels: DEBUG (development), INFO (normal), WARNING (recoverable), ERROR (unexpected), CRITICAL (system down)
- Include context: symbol, correlation_id, module name

## Testing

- File: `tests/test_<module>.py`
- Class: `Test<Feature>`
- Method: `test_<scenario>_<expected>`
- One assertion per test where possible
- Use fixtures for shared state, not setUpClass
- Mock external services (CCXT, YFinance)
- Test persistence boundaries with in-memory repositories

## Imports

Order (separated by blank line):
1. Standard library
2. Third-party
3. First-party (`traderos.*`)

```python
import os
from datetime import datetime

import pandas as pd

from traderos.domain.analysis.indicators import MarketAnalyzer
from traderos.infrastructure.config.config_loader import config
```

## Dependency Rules (Enforced)

```
Layer violations are BLOCKERS.

interface → application → domain
                              ↑
infrastructure ───────────────┘

Domain MUST NOT import:
  - sqlite3, requests, flask, django
  - Any infrastructure package
  - Application layer
```

## Review Checklist

- [ ] Types are complete and correct
- [ ] No `Any` used where concrete type exists
- [ ] No bare `except:` clauses
- [ ] Logger uses `%s` formatting
- [ ] No circular imports
- [ ] Layer dependencies respected
- [ ] Tests exist for new functionality
- [ ] No secrets or keys in code
- [ ] Docstrings on public API (one-liner sufficient)
- [ ] Formatting matches black output

## References
- [C:10] Engineering Standards — full standard reference
- `pyproject.toml` — tool configuration
- `.pre-commit-config.yaml` — automated enforcement
- `.ai/context/01_architecture.md` — dependency rules

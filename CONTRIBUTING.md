## Development Setup

```bash
git clone https://github.com/dmuhoro/TraderOS.git
cd TraderOS
python3.11 -m venv venv && source venv/bin/activate
pip install -e .[all,dev]
pre-commit install
```

## Code Standards

- Python 3.11+ with `from __future__ import annotations`
- Type hints everywhere — pyright strict mode enforced
- No `assert` in production code (disabled under `-O`)
- Domain layer has zero infrastructure imports (Dependency Inversion)
- 100-character line limit (black + ruff enforced)

## Making Changes

1. Branch from `main` (or the active sprint branch)
2. Write tests first where practical
3. Run `make format && make lint && make typecheck && make test`
4. Keep coverage ≥70%

## Commit Convention

Follow existing pattern:
```
Layer N: Short description — detail, detail, detail

- Bullet points describing specific changes
- Reference any related issues
```
## Running Tests

```bash
make test         # Full suite + coverage report
make test-fast    # Unit tests only (skip integration)
```

## Project Structure

- `domain/` — Pure business logic with zero infrastructure imports
- `application/` — Orchestration and composition root
- `infrastructure/` — Concrete implementations (DB, broker, config)
- `interfaces/` — Entry points (CLI, API)
- `tests/` — Mirrors `src/` structure

## Architecture Rules

1. **Domain depends on nothing** — no imports from `infrastructure` or `application`
2. **Ports in domain, impls in infrastructure** — domain defines protocols, infra implements them
3. **Factory is the composition root** — all wiring happens in `application/factory.py`
4. **No global mutable state** — everything injected through factory
5. **Secrets only from env vars** — never from config files

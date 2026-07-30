import ast
from pathlib import Path

DOMAIN_ROOT = Path(__file__).resolve().parent.parent.parent / "src" / "traderos" / "domain"
FIXTURE_PATH = Path(__file__).resolve().parent / "_fixture_broken_domain.py"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _iter_domain_py_files() -> list[Path]:
    if not DOMAIN_ROOT.exists():
        return []
    return sorted(DOMAIN_ROOT.rglob("*.py"))


def _extract_infra_imports(filepath: Path) -> list[tuple[int, str]]:
    """Return list of (line_number, import_statement) for any import
    that references traderos.infrastructure or any of its submodules."""
    violations: list[tuple[int, str]] = []
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"), filename=str(filepath))
    except SyntaxError:
        return violations
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("traderos.infrastructure") or alias.name.startswith(
                    "traderos.infrastructure."
                ):
                    violations.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            if node.module and (
                node.module.startswith("traderos.infrastructure")
                or node.module.startswith("traderos.infrastructure.")
            ):
                names = ", ".join(a.name for a in node.names)
                violations.append((node.lineno, f"from {node.module} import {names}"))
    return violations


class TestDomainDoesNotImportInfrastructure:
    def test_no_infrastructure_imports_in_domain(self) -> None:
        all_violations: list[tuple[str, int, str]] = []
        for pyfile in _iter_domain_py_files():
            if pyfile.name == "_fixture_broken_domain.py":
                continue
            relative = pyfile.relative_to(PROJECT_ROOT)
            violations = _extract_infra_imports(pyfile)
            for line_no, imp in violations:
                all_violations.append((str(relative), line_no, imp))
        assert (
            not all_violations
        ), "Domain layer MUST NOT import from infrastructure.\n" "Violations found:\n" + "\n".join(
            f"  {f}:{ln}  {stmt}" for f, ln, stmt in all_violations
        )

    def test_committed_fixture_is_detected_as_violation(self) -> None:
        """Regression fitness test: the committed fixture file contains a
        deliberate infrastructure import. If this test ever passes (0 violations),
        the fixture has been accidentally fixed and needs to be restored."""
        assert FIXTURE_PATH.exists(), f"Missing fixture: {FIXTURE_PATH}"
        violations = _extract_infra_imports(FIXTURE_PATH)
        assert len(violations) >= 1, (
            f"Fixture {FIXTURE_PATH} should have at least 1 infrastructure import violation, "
            f"but got {len(violations)}: {violations}. "
            "If you fixed the fixture, restore the deliberate violation!"
        )
        line_no, imp = violations[0]
        assert (
            "infrastructure" in imp
        ), f"Expected violation to mention 'infrastructure', got: {imp}"

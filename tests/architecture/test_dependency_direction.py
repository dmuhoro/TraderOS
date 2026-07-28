import ast
import tempfile
from pathlib import Path

DOMAIN_ROOT = Path(__file__).resolve().parent.parent.parent / "src" / "traderos" / "domain"
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
            relative = pyfile.relative_to(PROJECT_ROOT)
            violations = _extract_infra_imports(pyfile)
            for line_no, imp in violations:
                all_violations.append((str(relative), line_no, imp))
        assert (
            not all_violations
        ), "Domain layer MUST NOT import from infrastructure.\n" "Violations found:\n" + "\n".join(
            f"  {f}:{ln}  {stmt}" for f, ln, stmt in all_violations
        )

    def test_fixture_proves_check_can_fail(self) -> None:
        broken_code = (
            "from traderos.infrastructure.retry import retry_with_backoff\n"
            "class FakeDomainService:\n"
            "    pass\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "_fixture_broken_domain.py"
            fixture.write_text(broken_code)
            violations = _extract_infra_imports(fixture)
            assert len(violations) == 1, (
                "Fixture should have detected 1 infrastructure import violation,\n"
                f"but got {len(violations)}: {violations}"
            )
            line_no, imp = violations[0]
            assert (
                "infrastructure" in imp
            ), f"Expected violation to mention 'infrastructure', got: {imp}"

"""Package-level dependency rules for ``evotrias``.

Dependencies only point downward: ``mcp -> evolve_ctrl -> db``,
``dashboard -> db``, ``hooks -> scheduler -> db``; ``db`` never imports an
upper layer.  These tests read every module with ``ast`` so a stray import
fails here, not in a code review.
"""

from __future__ import annotations

import ast
from pathlib import Path

import evotrias

PACKAGE_ROOT = Path(evotrias.__file__).resolve().parent


def _top_level(name: str) -> str | None:
    parts = name.split(".")
    if parts[0] != "evotrias" or len(parts) < 2:
        return None
    return parts[1]


def _imports_by_module() -> dict[str, set[str]]:
    """Map each ``evotrias/**/*.py`` (relative to the package's parent directory, posix) to the evotrias top-level packages it imports."""
    result: dict[str, set[str]] = {}
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        targets: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = _top_level(alias.name)
                    if top:
                        targets.add(top)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                if node.module == "evotrias":
                    targets.update(alias.name for alias in node.names)
                else:
                    top = _top_level(node.module)
                    if top:
                        targets.add(top)
        result[path.relative_to(PACKAGE_ROOT.parent).as_posix()] = targets
    return result


def _violations(prefix: str, forbidden: set[str], *, allow: set[str] = frozenset()) -> list[str]:
    return [
        f"{module}: {sorted(targets & forbidden)}"
        for module, targets in _imports_by_module().items()
        if module.startswith(prefix) and module not in allow and targets & forbidden
    ]


def test_mcp_does_not_touch_db():
    assert _violations("evotrias/mcp/", {"db"}) == []


def test_dashboard_only_reads_db():
    assert _violations("evotrias/dashboard/", {"evolve_ctrl", "mcp", "hooks", "permissions", "scheduler"}) == []


def test_db_does_not_depend_on_upper_layers():
    upper = {"evolve_ctrl", "mcp", "hooks", "permissions", "scheduler", "dashboard", "logger"}
    assert _violations("evotrias/db/", upper) == []


def test_evolve_ctrl_does_not_import_mcp_or_hooks():
    assert _violations("evotrias/evolve_ctrl/", {"mcp", "hooks", "permissions", "dashboard"}) == []


def test_harness_does_not_depend_on_runtime_layers():
    # harness only describes workspace files and launch commands; it must not pull in the runtime.
    runtime = {"hooks", "mcp", "db", "evolve_ctrl", "dashboard", "scheduler", "permissions"}
    assert _violations("evotrias/setup/harness/", runtime) == []


def _setup_modules_imported(module_path: Path) -> set[str]:
    """``evotrias.setup.<name>`` modules (by ``name``) a file imports."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(module_path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules = [f"{node.module}.{alias.name}" for alias in node.names]
        else:
            continue
        for module in modules:
            parts = module.split(".")
            if parts[:2] == ["evotrias", "setup"] and len(parts) > 2:
                names.add(parts[2])
    return names


def test_harness_does_not_import_the_rest_of_setup():
    # setup (the installer) builds on harness descriptions, never the other way round.
    violations = {
        path.relative_to(PACKAGE_ROOT.parent).as_posix(): sorted(_setup_modules_imported(path) - {"harness"})
        for path in sorted((PACKAGE_ROOT / "setup" / "harness").rglob("*.py"))
    }
    assert {module: names for module, names in violations.items() if names} == {}

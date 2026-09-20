"""Package-level dependency rules for ``evotrias``.

Dependencies only point downward: ``mcp -> evolve_ctrl -> db``,
``dashboard -> db``, ``hooks -> scheduler -> db``; ``db`` never imports an
upper layer.  These tests read every module with ``ast`` so a stray import
fails here, not in a code review.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, Set

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "evotrias"


def _top_level(name: str) -> str | None:
    parts = name.split(".")
    if parts[0] != "evotrias" or len(parts) < 2:
        return None
    return parts[1]


def _imports_by_module() -> Dict[str, Set[str]]:
    """Map each ``evotrias/**/*.py`` (repo-relative, posix) to the evotrias top-level packages it imports."""
    result: Dict[str, Set[str]] = {}
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        targets: Set[str] = set()
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
        result[path.relative_to(REPO_ROOT).as_posix()] = targets
    return result


def _violations(prefix: str, forbidden: Set[str], *, allow: Set[str] = frozenset()) -> list[str]:
    return [
        f"{module}: {sorted(targets & forbidden)}"
        for module, targets in _imports_by_module().items()
        if module.startswith(prefix) and module not in allow and targets & forbidden
    ]


def test_mcp_does_not_touch_db():
    # dashboard.py reads db files directly until it moves to evotrias/dashboard
    # (TODO block 3); drop the allowance with that move.
    assert _violations("evotrias/mcp/", {"db"}, allow={"evotrias/mcp/dashboard.py"}) == []


def test_db_does_not_depend_on_upper_layers():
    upper = {"evolve_ctrl", "mcp", "hooks", "permissions", "scheduler", "dashboard", "logger"}
    assert _violations("evotrias/db/", upper) == []


def test_evolve_ctrl_does_not_import_mcp_or_hooks():
    assert _violations("evotrias/evolve_ctrl/", {"mcp", "hooks", "permissions", "dashboard"}) == []

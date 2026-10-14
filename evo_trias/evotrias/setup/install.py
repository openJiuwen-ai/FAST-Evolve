"""``install(harness, workspace)``: set a workspace up for one harness (design.md §6.3)."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from evotrias.db import paths
from evotrias.harness.base import Harness
from evotrias.setup.install_modes import STRATEGIES

TEMPLATES = files("evotrias") / "templates"


def install(harness: Harness, workspace: Path) -> list[Path]:
    """Apply every install entry of ``harness`` to ``workspace``, then record it as the active harness.
    Returns the files written; files outside the template list are never touched or deleted."""
    context = harness.template_context()
    written: list[Path] = []
    for src, dst, mode in harness.install_entries:
        written += STRATEGIES[mode](TEMPLATES / src, workspace / dst, context)
    active = workspace / paths.ACTIVE_HARNESS
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_bytes(f"{harness.name}\n".encode())
    return written + [active]

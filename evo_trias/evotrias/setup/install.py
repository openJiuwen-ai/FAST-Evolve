"""``install(harness, workspace)``: set a workspace up for one harness (design.md §6.3)."""

from __future__ import annotations

from collections.abc import Iterator
from importlib.resources import files
from pathlib import Path

from evotrias.db import paths
from evotrias.harness.base import Harness
from evotrias.setup.install_modes import STRATEGIES

TEMPLATES = files("evotrias") / "templates"


def install(harness: Harness, workspace: Path) -> Iterator[tuple[str, list[Path]]]:
    """Apply the install entries of ``harness`` to ``workspace`` one by one, then record it as the
    active harness.  Yields ``(destination, files written)`` after each step, so a caller can show
    progress.  Lazy: nothing is installed unless the caller iterates.  Files outside the template
    list are never touched or deleted."""
    context = harness.template_context()
    for src, dst, mode in harness.install_entries:
        yield dst, STRATEGIES[mode](TEMPLATES / src, workspace / dst, context)
    active = workspace / paths.ACTIVE_HARNESS
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_bytes(f"{harness.name}\n".encode())
    yield paths.ACTIVE_HARNESS.as_posix(), [active]

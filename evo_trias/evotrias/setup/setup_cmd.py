"""Non-interactive ``evotrias setup <harness>``."""

from __future__ import annotations

import sys
from pathlib import Path

from evotrias.setup.harness.registry import get_harness
from evotrias.setup.install import install
from evotrias.setup.workspace import read_active_harness


def setup_cmd(name: str, workspace: Path) -> int:
    """Set ``workspace`` up for the harness ``name``; 1 without writing anything if it is not found."""
    harness = get_harness(name)
    found = harness.detect()
    if found is None:
        print(
            f'"{harness.executable}" not found or not runnable. '
            f'Install it, then re-run "evotrias setup {harness.name}".',
            file=sys.stderr,
        )
        return 1
    previous = read_active_harness(workspace)
    if previous is not None:
        print(
            f"warning: this directory is already set up for {previous}; setting it up for {harness.name}.",
            file=sys.stderr,
        )
    for _step in install(harness, workspace):
        pass
    print(f"Set up for {harness.name} ({found}).")
    print('Run "evotrias" to start.')
    return 0

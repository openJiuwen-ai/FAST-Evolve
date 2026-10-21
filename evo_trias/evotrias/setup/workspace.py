# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Which harness a workspace is set up for: the only reader and writer of ``.evotrias/active_harness``."""

from __future__ import annotations

from pathlib import Path

# relative to the workspace; plain text, the harness name and a newline
ACTIVE_HARNESS = Path(".evotrias") / "active_harness"


def read_active_harness(workspace: Path) -> str | None:
    """Name of the harness ``workspace`` is set up for; None when it is not a workspace."""
    path = workspace / ACTIVE_HARNESS
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip()


def write_active_harness(workspace: Path, name: str) -> None:
    """Record ``name`` as the harness ``workspace`` is set up for."""
    path = workspace / ACTIVE_HARNESS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"{name}\n".encode())

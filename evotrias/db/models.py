"""Data models: run configuration and persisted records.

Transitional home for the dataclasses formerly in ``evotrias.mcp.models``.
Each class moves into its own ``evotrias.db.<file>`` module as that store is
migrated; this file is deleted once empty.

The controller is intentionally task-agnostic.  It does not know whether a
record came from kernel generation, code repair, research search, or another
domain.  The only optimizer-facing quality signal is ``fitness``, and higher
fitness is always better.  Domain-specific measurements belong in ``profile``
or ``metrics`` and are preserved for the host agent to interpret.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LineageStats:
    """Aggregate statistics for one lineage root."""

    root_id: str
    size: int = 0
    best_fitness: float = float("-inf")
    best_record_id: str = ""
    max_depth: int = 0
    share: float = 0.0
    total_selections: int = 0
    recent_improvement: float = 0.0

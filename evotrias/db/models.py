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

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from evotrias.utils.time import utc_now_iso


@dataclass
class SuccessRecord:
    """One successful attempt that can be reused as parent or inspiration.

    ``SuccessRecord`` is a single item in the success pool.  It is deliberately
    generic: the controller only sorts and samples by ``fitness``.  Everything
    else is descriptive context for the host agent or downstream analysis.
    """

    id: str
    run_id: str
    fitness: float
    parent_id: Optional[str] = None
    generation: int = 0
    selection_count: int = 0
    artifact_ref: str = ""
    sketch: str = ""
    profile: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    task_id: Optional[str] = None
    subagent_id: Optional[str] = None
    verification_result_ref: Optional[str] = None
    created_at: str = field(default_factory=utc_now_iso)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the record to JSON-compatible data."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SuccessRecord":
        """Build a record from persisted JSON-compatible data.

        This method is intentionally tolerant of missing optional fields so the
        on-disk SuccessDB can evolve without breaking older runs.
        """
        return cls(
            id=str(data["id"]),
            run_id=str(data["run_id"]),
            fitness=float(data["fitness"]),
            parent_id=data.get("parent_id"),
            generation=int(data.get("generation", 0)),
            selection_count=int(data.get("selection_count", 0)),
            artifact_ref=str(data.get("artifact_ref", "")),
            sketch=str(data.get("sketch", "")),
            profile=dict(data.get("profile") or {}),
            metrics=dict(data.get("metrics") or {}),
            task_id=data.get("task_id"),
            subagent_id=data.get("subagent_id"),
            verification_result_ref=data.get("verification_result_ref"),
            created_at=str(data.get("created_at") or utc_now_iso()),
            metadata=dict(data.get("metadata") or {}),
        )

    def to_inspiration(self) -> Dict[str, Any]:
        """Return the compact shape sent to generator subagents."""
        return {
            "id": self.id,
            "artifact_ref": self.artifact_ref,
            "sketch": self.sketch,
            "fitness": self.fitness,
            "profile": self.profile,
            "metrics": self.metrics,
            "generation": self.generation,
            "parent_id": self.parent_id,
        }

    def to_summary(self) -> Dict[str, Any]:
        """Return the parent summary shape used by ``select_parent``."""
        return {
            "id": self.id,
            "fitness": self.fitness,
            "artifact_ref": self.artifact_ref,
            "sketch": self.sketch,
            "profile": self.profile,
            "metrics": self.metrics,
            "generation": self.generation,
            "parent_id": self.parent_id,
            "selection_count": self.selection_count,
        }


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

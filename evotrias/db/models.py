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
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    """Return an ISO timestamp with timezone information for persisted records."""
    return datetime.now(timezone.utc).isoformat()


class SearchState(Enum):
    """Internal search state maintained by the controller."""

    EXPLORING = "exploring"
    WATCHING = "watching"
    STOPPED = "stopped"


@dataclass
class ParentSelectionConfig:
    """Configuration for the lineage-aware parent selector.

    The selector first chooses a lineage using a soft-island policy, then picks
    a concrete record inside that lineage using local UCB.  The ``*_start`` and
    ``*_end`` values are annealed by search progress in ``[0, 1]``.
    """

    tau_start: float = 1.5
    tau_end: float = 0.5
    c_local_start: float = 1.5
    c_local_end: float = 0.5
    p_exploit: float = 0.7
    quality_bonus_strength: float = 0.3
    elite_rank_fraction: float = 0.15
    elite_selection_shield: float = 0.6


@dataclass
class InspirationSelectionConfig:
    """Configuration for Boltzmann inspiration sampling with migration."""

    inspiration_sample_num: int = 3
    p_migrate: float = 0.2
    foreign_elite_num: int = 2
    T_start: float = 1.5
    T_end: float = 0.5


@dataclass
class RunBudgetConfig:
    """Hard run limits that stop task replenishment."""

    max_total_tasks: int = 100
    max_time_seconds: Optional[float] = None


@dataclass
class SchedulerConfig:
    """Task-pool shape confirmed at configure_run time.

    ``lineage_root_number`` is the number of initial exploration directions
    and therefore the number of lineage roots the run establishes.  It bounds
    the root phase: at most this many tasks are minted before all of them must
    finish (the root barrier).  ``max_concurrent_tasks`` is the in-flight task
    target that holds in every phase, root phase included.  The two are
    deliberately independent knobs.
    """

    lineage_root_number: int = 4
    max_concurrent_tasks: int = 4


@dataclass
class PlateauConfig:
    """Configuration for fitness-plateau detection."""

    window: int = 5
    improvement_threshold: float = 0.0
    patience: int = 4


@dataclass
class MetaEvolveConfig:
    """Configuration for lightweight meta-guidance control."""

    enabled: bool = True
    guidance_num_to_generate: int = 3
    guidance_max_uses: int = 2


@dataclass
class EvolutionControllerConfig:
    """Top-level controller configuration grouped by solver."""

    parent_selection: ParentSelectionConfig = field(default_factory=ParentSelectionConfig)
    inspiration: InspirationSelectionConfig = field(default_factory=InspirationSelectionConfig)
    budget: RunBudgetConfig = field(default_factory=RunBudgetConfig)
    plateau: PlateauConfig = field(default_factory=PlateauConfig)
    meta_evolve: MetaEvolveConfig = field(default_factory=MetaEvolveConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)


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


@dataclass
class AttemptRecord:
    """A completed task attempt, successful or not.

    Attempts are broader than SuccessRecords.  Every final task result should be
    recorded as an attempt for audit and future policy evidence.  Only attempts
    with successful verification and a valid fitness enter SuccessDB.
    """

    id: str
    run_id: str
    task_id: str
    subagent_id: str
    artifact_ref: str
    parent_id: Optional[str]
    inspiration_ids: List[str]
    generation: Optional[int]
    verification_result: Dict[str, Any]
    meta_guidance_id: Optional[str] = None
    anchor_parent_id: Optional[str] = None
    verification_result_ref: Optional[str] = None
    sketch: Optional[str] = None
    entered_success_db: bool = False
    success_record_id: Optional[str] = None
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the attempt to JSONL-compatible data."""
        return asdict(self)


@dataclass
class TaskContext:
    """Pre-record mapping from one task to selected evolutionary context.

    The public MCP contract keeps parent and inspiration ids out of later tool
    calls.  This small persisted object is the bridge: selection tools fill it,
    and ``record_attempt`` reads it back when the verifier result is ready.
    """

    task_id: str
    parent_id: Optional[str] = None
    inspiration_ids: List[str] = field(default_factory=list)
    meta_guidance_id: Optional[str] = None
    anchor_parent_id: Optional[str] = None
    parent_selected: bool = False
    inspirations_selected: bool = False
    missing_parent_context: bool = False
    recorded: bool = False
    verification_result_ref: Optional[str] = None
    entered_success_db: Optional[bool] = None
    success_record_id: Optional[str] = None
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def touch(self) -> None:
        """Refresh the update timestamp before persistence."""
        self.updated_at = utc_now_iso()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the context to JSON-compatible data."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskContext":
        """Build a context from persisted JSON-compatible data."""
        return cls(
            task_id=str(data["task_id"]),
            parent_id=data.get("parent_id"),
            inspiration_ids=list(data.get("inspiration_ids") or []),
            meta_guidance_id=data.get("meta_guidance_id"),
            anchor_parent_id=data.get("anchor_parent_id"),
            parent_selected=bool(data.get("parent_selected", False)),
            inspirations_selected=bool(data.get("inspirations_selected", False)),
            missing_parent_context=bool(data.get("missing_parent_context", False)),
            recorded=bool(data.get("recorded", False)),
            verification_result_ref=data.get("verification_result_ref"),
            entered_success_db=data.get("entered_success_db"),
            success_record_id=data.get("success_record_id"),
            created_at=str(data.get("created_at") or utc_now_iso()),
            updated_at=str(data.get("updated_at") or utc_now_iso()),
        )


@dataclass
class RunState:
    """Controller-owned run metadata that should not enter agent context."""

    run_id: str
    started_at: str = field(default_factory=utc_now_iso)
    task_description: str = ""
    search_state: str = SearchState.EXPLORING.value
    stop_reason: str = ""
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    last_checked_attempt_count: int = 0
    run_config: Dict[str, Any] = field(default_factory=dict)
    updated_at: str = field(default_factory=utc_now_iso)

    def touch(self) -> None:
        """Refresh the update timestamp before persistence."""
        self.updated_at = utc_now_iso()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the run state to JSON-compatible data."""
        return asdict(self)

    @classmethod
    def from_dict(cls, run_id: str, data: Dict[str, Any]) -> "RunState":
        """Build run state from persisted JSON-compatible data."""
        return cls(
            run_id=str(data.get("run_id") or run_id),
            started_at=str(data.get("started_at") or utc_now_iso()),
            task_description=str(data.get("task_description") or ""),
            search_state=str(data.get("search_state") or SearchState.EXPLORING.value),
            stop_reason=str(data.get("stop_reason") or ""),
            diagnostics=dict(data.get("diagnostics") or {}),
            last_checked_attempt_count=int(data.get("last_checked_attempt_count", 0)),
            run_config=dict(data.get("run_config") or {}),
            updated_at=str(data.get("updated_at") or utc_now_iso()),
        )

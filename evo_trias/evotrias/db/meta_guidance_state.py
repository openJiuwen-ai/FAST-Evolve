"""Meta-guidance lifecycle state, persisted as ``meta_guidance_state.json``.

The source of truth for the meta-evolve loop: the plateau watch window, the
one outstanding request to the meta-guidance-generator agent, every guidance
it has committed (active or exhausted) and which task each guidance was
assigned to.  ``meta_guidance_events.jsonl`` is the derived timeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evotrias.db import paths
from evotrias.db.base import DBObject
from evotrias.utils.time import utc_now_iso


@dataclass
class Watching:
    """Where the current plateau watch started."""

    started_task_count: int = 0
    best_fitness: float | None = None
    created_at: str = field(default_factory=utc_now_iso)


@dataclass
class RequestProgress:
    best_fitness_history: list[float] = field(default_factory=list)
    completed_since_watching: int = 0
    best_at_watching_start: float | None = None
    current_best_fitness: float | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass
class PendingRequest:
    """The full payload handed to the meta-guidance-generator agent.

    ``current_best``, ``lineages``, ``notes_summary``, ``guidance_history`` and
    ``failed_guidance`` are produced by other modules and carried verbatim.
    """

    request_id: str
    state: str = "pending"
    created_at: str = field(default_factory=utc_now_iso)
    created_at_task_count: int = 0
    task_description: str = ""
    current_best: dict[str, Any] = field(default_factory=dict)
    progress: RequestProgress = field(default_factory=RequestProgress)
    lineages: list[dict[str, Any]] = field(default_factory=list)
    notes_summary: dict[str, Any] = field(default_factory=dict)
    guidance_history: list[dict[str, Any]] = field(default_factory=list)
    failed_guidance: list[dict[str, Any]] = field(default_factory=list)
    generation_instructions: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)


@dataclass
class Guidance:
    """One strategy idea committed by the agent, plus how it fared.

    The first block is what the agent submitted (validated by
    ``_normalize_guidance_item``); the rest is controller bookkeeping.
    """

    idea: str
    description: str
    novelty_claim: str
    approach_type: str
    target_metric: str = "fitness"
    current_best_assessment: str = ""
    relationship_to_current_best: str = "critically_adapt"
    implementation_plan: list[str] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)
    avoid_repeating: list[str] = field(default_factory=list)
    max_uses: int = 1

    guidance_id: str = ""
    request_id: str = ""
    anchor_parent_id: str | None = None
    creation_anchor_parent_id: str | None = None
    uses: int = 0
    status: str = "active"
    created_at: str = field(default_factory=utc_now_iso)
    created_at_task_count: int | None = None
    best_fitness_at_creation: float | None = None
    has_valid_result: bool = False
    improved_anchor: bool = False
    created_new_best: bool = False
    outcomes: list[dict[str, Any]] = field(default_factory=list)
    outcome: str | None = None
    outcome_summary: dict[str, Any] | None = None


@dataclass
class Assignment:
    """A guidance handed to one task; result fields are filled by record_attempt_outcome."""

    task_id: str
    guidance_id: str
    anchor_parent_id: str
    anchor_fitness: float | None = None
    creation_anchor_parent_id: str | None = None
    status: str = "assigned"
    assigned_at: str = field(default_factory=utc_now_iso)

    attempt_id: str | None = None
    valid_result: bool | None = None
    in_success_pool: bool | None = None
    success_record_id: str | None = None
    fitness: float | None = None
    delta_vs_anchor: float | None = None
    relative_delta_vs_anchor: float | None = None
    improved_anchor: bool | None = None
    metrics_changed: bool | None = None
    no_effect: bool | None = None
    best_fitness_before: float | None = None
    best_fitness_after: float | None = None
    created_new_best: bool | None = None
    recorded_at: str | None = None


class MetaGuidanceState(DBObject):
    watching: Watching | None = None
    pending_request: PendingRequest | None = None
    active_guidance: list[Guidance] = field(default_factory=list)
    tried_guidance: list[Guidance] = field(default_factory=list)
    task_assignments: dict[str, Assignment] = field(default_factory=dict)
    next_guidance_index: int = 1
    next_request_index: int = 1

    def __init__(self, run_id: str):
        super().__init__(paths.run_dir(run_id) / paths.META_GUIDANCE_STATE)

    def active_by_id(self, guidance_id: str) -> Guidance | None:
        return next((g for g in self.active_guidance if g.guidance_id == guidance_id), None)

    @staticmethod
    def next_id(prefix: str, number: int) -> str:
        return f"{prefix}_{number:04d}"

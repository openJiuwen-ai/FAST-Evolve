"""The reusable success pool: every task that produced a valid candidate.

``SuccessPool`` is an in-memory view derived on construction from two db
files -- ``attempts.jsonl`` (the attempts flagged ``in_success_pool``) and
``task_contexts.json`` (how often each task was chosen as a parent).  It is
never written to disk; ``record_attempt`` appends attempts and the next
``SuccessPool(run_id)`` reflects them.

Parent selection and inspiration selection only read from this pool, so
failed attempts cannot become parents.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from evotrias.db.attempts import AttemptRecord, Attempts
from evotrias.db.task_contexts import TaskContexts
from evotrias.utils.time import utc_now_iso


@dataclass
class SuccessRecord:
    """One successful attempt that can be reused as parent or inspiration.

    Deliberately generic: the controller only sorts and samples by
    ``fitness``.  Everything else is descriptive context for the host agent or
    downstream analysis.  Built only by ``from_attempt``.
    """

    id: str
    run_id: str
    fitness: float
    parent_id: str | None = None
    generation: int = 0
    selection_count: int = 0
    artifact_ref: str = ""
    sketch: str = ""
    profile: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    subagent_id: str | None = None
    verification_result_ref: str | None = None
    created_at: str = field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_attempt(cls, attempt: AttemptRecord, selection_count: int = 0) -> SuccessRecord:
        verification = attempt.verification_result or {}
        metrics = dict(verification.get("metrics") or {})
        return cls(
            id=attempt.task_id,
            run_id=attempt.run_id,
            fitness=float(verification["fitness"]),
            parent_id=attempt.parent_id,
            generation=attempt.generation or 0,
            selection_count=selection_count,
            artifact_ref=attempt.artifact_ref,
            sketch=str(verification.get("summary") or verification.get("feedback") or ""),
            profile=dict(verification.get("profile") or {}) or metrics,
            metrics=metrics,
            task_id=attempt.task_id,
            subagent_id=attempt.subagent_id,
            verification_result_ref=attempt.verification_result_ref,
            created_at=attempt.created_at,
            metadata={
                "attempt_id": attempt.id,
                "inspiration_ids": list(attempt.inspiration_ids),
                "evaluator_version": verification.get("evaluator_version"),
                "log_ref": verification.get("log_ref"),
                "summary_ref": verification.get("summary_ref"),
                "report_ref": verification.get("report_ref"),
            },
        )


class SuccessPool:
    def __init__(self, run_id: str):
        self.run_id = run_id
        contexts = TaskContexts(run_id).tasks
        selections = Counter(
            context.parent_id for context in contexts.values() if context.parent_selected and context.parent_id
        )
        self._records: dict[str, SuccessRecord] = {
            task_id: SuccessRecord.from_attempt(attempt, selections[task_id])
            for task_id, attempt in Attempts(run_id).successes.items()
        }

    def __contains__(self, record_id: object) -> bool:
        return record_id in self._records

    def is_empty(self) -> bool:
        """Return true when no successful candidate is available."""
        return not self._records

    def get_all(self) -> list[SuccessRecord]:
        """Return all records in a stable chronological order."""
        return sorted(self._records.values(), key=lambda record: record.created_at)

    def get(self, record_id: str | None) -> SuccessRecord | None:
        """Return one record by id, or None for missing/null ids."""
        if record_id is None:
            return None
        return self._records.get(record_id)

    def get_best_record(self) -> SuccessRecord | None:
        """Return the record with highest fitness."""
        if not self._records:
            return None
        return max(self._records.values(), key=lambda record: record.fitness)

    def get_canonical_best_record(self) -> SuccessRecord | None:
        """Return a deterministic best record for use as an anchor parent."""
        if not self._records:
            return None
        return min(
            self._records.values(),
            key=lambda record: (-record.fitness, record.created_at, record.id),
        )

    def infer_next_generation(self, parent_id: str | None) -> int:
        """Infer a child generation from its parent when the caller omitted it."""
        parent = self.get(parent_id)
        if parent is None:
            return 0
        return parent.generation + 1

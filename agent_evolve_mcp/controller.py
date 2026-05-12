"""Internal facade for the Evolution Controller MCP.

The public tools expose a deliberately small schema.  This controller owns the
larger local state needed to make that possible: SuccessDB, attempt log, task
context cache, run state, lineage indexes, and the three strategy solvers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from agent_evolve_mcp.config import load_evolution_controller_config
from agent_evolve_mcp.convergence_detector import MultiDimensionalConvergenceDetector
from agent_evolve_mcp.inspiration_selector import LineageBoltzmannInspirationSelector
from agent_evolve_mcp.lineage_tree import LineageTree
from agent_evolve_mcp.models import AttemptRecord, EvolutionControllerConfig, RunState, SearchState, SuccessRecord
from agent_evolve_mcp.parent_selector import LineageAwareParentSelector
from agent_evolve_mcp.state_store import RunStateStore, TaskContextStore
from agent_evolve_mcp.success_db import AttemptLog, SuccessDB
from agent_evolve_mcp.run_report import write_run_report


class EvolutionController:
    """Coordinate persistence, lineage indexing, selectors, and convergence."""

    def __init__(
        self,
        run_id: str,
        config: Optional[EvolutionControllerConfig] = None,
    ):
        """Create a controller bound to one run id.

        The controller object itself is only a convenience cache.  Every public
        operation reloads disk-backed state before making a decision.
        """
        self.run_id = run_id
        self.config = config or load_evolution_controller_config()
        self.db = SuccessDB(run_id)
        self.attempt_log = AttemptLog(run_id)
        self.task_contexts = TaskContextStore(run_id)
        self.run_state_store = RunStateStore(run_id)
        self.lineage_tree = LineageTree()
        self.lineage_tree.rebuild(self.db)
        self.parent_selector = LineageAwareParentSelector(
            self.config.parent_selection,
            self.lineage_tree,
        )
        self.inspiration_selector = LineageBoltzmannInspirationSelector(
            self.config.inspiration,
            self.lineage_tree,
        )
        self.convergence_detector = MultiDimensionalConvergenceDetector(
            self.config.convergence,
        )

    def select_parent(self, task_id: str) -> Dict[str, Any]:
        """Return the minimal parent ref for one generator task.

        The first call for a task samples and caches a parent.  Later retries
        return the cached parent without incrementing selection count again.
        """
        self._load_state()
        context = self.task_contexts.get(task_id)
        if context is not None and context.parent_selected:
            return self._parent_ref(context.parent_id)

        parent = self.parent_selector.select(
            self.db,
            self._progress(),
            signal=self.convergence_detector.get_strategy_signal(),
        )

        context = self.task_contexts.ensure(task_id)
        context.parent_id = parent.id if parent is not None else None
        context.parent_selected = True
        self.task_contexts.put(context)
        return self._parent_ref(context.parent_id)

    def select_inspirations(self, task_id: str) -> Dict[str, Any]:
        """Return minimal inspiration refs for one generator task.

        The public response intentionally excludes the parent.  The parent was
        already returned by ``select_parent`` and is stored in task context for
        later attempt recording.
        """
        self._load_state()
        context = self.task_contexts.get(task_id)
        if context is None or not context.parent_selected:
            raise ValueError("Call evolve_select_parent before evolve_select_inspirations for this task.")

        if context.inspirations_selected:
            return self._inspiration_refs(context.inspiration_ids)

        if context.parent_id is None:
            context.inspiration_ids = []
            context.inspirations_selected = True
            self.task_contexts.put(context)
            return {"inspirations": []}

        parent = self.db.get(context.parent_id)
        if parent is None:
            raise ValueError(f"Cached parent {context.parent_id!r} is not present in SuccessDB.")

        inspirations = self.inspiration_selector.select(
            parent,
            self.db,
            self._progress(),
            signal=self.convergence_detector.get_strategy_signal(),
        )
        context.inspiration_ids = [
            str(item["id"])
            for item in inspirations
            if not item.get("is_parent") and item.get("id") != parent.id
        ]
        context.inspirations_selected = True
        self.task_contexts.put(context)
        return self._inspiration_refs(context.inspiration_ids)

    def record_attempt(self, task_id: str, verification_result_ref: str) -> Dict[str, Any]:
        """Record a final verifier result and optionally add it to SuccessDB.

        The verifier result is read from disk through ``verification_result_ref``.
        Parent and inspiration ids are recovered from task context, keeping the
        public MCP call small and less error-prone.
        """
        self._load_state()
        context = self.task_contexts.ensure(task_id)

        if context.recorded:
            if (
                context.verification_result_ref
                and context.verification_result_ref != verification_result_ref
            ):
                raise ValueError(
                    "Task has already been recorded with a different verification_result_ref."
                )
            return _record_response(context.entered_success_db, context.success_record_id)

        existing_attempt = self.attempt_log.find_by_task_id(task_id)
        if existing_attempt is not None:
            existing_ref = existing_attempt.get("verification_result_ref")
            if existing_ref and existing_ref != verification_result_ref:
                raise ValueError(
                    "Attempt log already contains this task with a different verification_result_ref."
                )
            context.recorded = True
            context.verification_result_ref = existing_ref or verification_result_ref
            context.entered_success_db = bool(existing_attempt.get("entered_success_db", False))
            context.success_record_id = existing_attempt.get("success_record_id")
            self.task_contexts.put(context)
            return _record_response(context.entered_success_db, context.success_record_id)

        verification_result = _load_verification_result(verification_result_ref)
        artifact_ref = str(verification_result.get("artifact_ref") or "")
        if not artifact_ref:
            raise ValueError("verification_result_ref JSON must contain artifact_ref.")

        attempt_id = str(verification_result.get("attempt_id") or task_id or uuid4())
        subagent_id = str(verification_result.get("subagent_id") or "")
        parent_id = context.parent_id if context.parent_selected else None
        inspiration_ids = list(context.inspiration_ids)
        generation = self.db.infer_next_generation(parent_id)

        status = str(verification_result.get("status", "")).lower()
        fitness = _extract_fitness(verification_result)
        entered_success_db = False
        success_record_id = None

        if status == "success":
            if fitness is None:
                # Successful verification without top-level fitness is audited
                # as an attempt, but it cannot become reusable search material.
                entered_success_db = False
            else:
                success_record_id = attempt_id
                if self.db.get(success_record_id) is not None:
                    entered_success_db = True
                else:
                    metrics = dict(verification_result.get("metrics") or {})
                    profile = dict(verification_result.get("profile") or {})
                    if not profile:
                        profile = metrics
                    record = SuccessRecord(
                        id=success_record_id,
                        run_id=self.run_id,
                        fitness=fitness,
                        parent_id=parent_id,
                        generation=generation,
                        artifact_ref=artifact_ref,
                        sketch=str(
                            verification_result.get("summary")
                            or verification_result.get("feedback")
                            or ""
                        ),
                        profile=profile,
                        metrics=metrics,
                        task_id=task_id,
                        subagent_id=subagent_id,
                        verification_result_ref=verification_result_ref,
                        metadata={
                            "inspiration_ids": inspiration_ids,
                            "evaluator_version": verification_result.get("evaluator_version"),
                            "log_ref": verification_result.get("log_ref"),
                            "summary_ref": verification_result.get("summary_ref"),
                            "report_ref": verification_result.get("report_ref"),
                        },
                    )
                    entered_success_db = self.db.add(record)

        attempt = AttemptRecord(
            id=attempt_id,
            run_id=self.run_id,
            task_id=task_id,
            subagent_id=subagent_id,
            artifact_ref=artifact_ref,
            parent_id=parent_id,
            inspiration_ids=inspiration_ids,
            generation=generation,
            verification_result=verification_result,
            verification_result_ref=verification_result_ref,
            sketch=str(verification_result.get("summary") or ""),
            entered_success_db=entered_success_db,
            success_record_id=success_record_id if entered_success_db else None,
        )
        self.attempt_log.append(attempt)

        context.recorded = True
        context.verification_result_ref = verification_result_ref
        context.entered_success_db = entered_success_db
        context.success_record_id = success_record_id if entered_success_db else None
        self.task_contexts.put(context)

        return _record_response(context.entered_success_db, context.success_record_id)

    def check_convergence(self) -> Dict[str, Any]:
        """Return only whether the host should stop submitting new tasks."""
        self._load_state()
        run_state = self.run_state_store.load()
        total_submitted = self.attempt_log.count()
        total_success = len(self.db.get_all())
        elapsed_seconds = self.run_state_store.elapsed_seconds()
        should_stop, reason = self.convergence_detector.evaluate(
            self.db,
            self.lineage_tree,
            total_submitted=total_submitted,
            total_success=total_success,
            elapsed_seconds=elapsed_seconds,
        )

        self.run_state_store.save(
            RunState(
                run_id=self.run_id,
                started_at=run_state.started_at,
                search_state=self.convergence_detector.state.value,
                stop_reason=reason,
                diagnostics=self.convergence_detector.get_diagnostics(),
                last_checked_attempt_count=total_submitted,
            )
        )
        return {"should_stop": should_stop}

    def finalize_run(self, stop_reason: Optional[str] = None) -> Dict[str, Any]:
        """Write deterministic run summary artifacts and return compact refs.

        Finalization is intentionally idempotent.  Repeated calls regenerate the
        same report files from current persisted state without changing
        SuccessDB or attempt history.
        """
        self._load_state()
        run_state = self.run_state_store.load()
        final_reason = stop_reason or run_state.stop_reason or "manual_finalize"
        finalized_state = RunState(
            run_id=self.run_id,
            started_at=run_state.started_at,
            search_state=SearchState.STOPPED.value,
            stop_reason=final_reason,
            diagnostics=run_state.diagnostics,
            last_checked_attempt_count=self.attempt_log.count(),
        )
        self.run_state_store.save(finalized_state)
        self._load_state()
        return write_run_report(
            run_id=self.run_id,
            db=self.db,
            lineage_tree=self.lineage_tree,
            run_state=self.run_state_store.load(),
        )

    def _load_state(self) -> None:
        """Reload all disk-backed state needed for a decision."""
        self.db.load()
        self.task_contexts.load()
        run_state = self.run_state_store.load()
        self.convergence_detector.restore_state(
            run_state.search_state,
            stop_reason=run_state.stop_reason,
            diagnostics=run_state.diagnostics,
        )
        self.lineage_tree.rebuild(self.db)

    def _progress(self) -> float:
        """Compute search progress internally from recorded attempts."""
        max_total = max(self.config.convergence.max_total_tasks, 1)
        return min(self.attempt_log.count() / max_total, 1.0)

    def _parent_ref(self, parent_id: Optional[str]) -> Dict[str, Any]:
        """Return the minimal public parent payload."""
        if parent_id is None:
            return {"parent_id": None, "artifact_ref": None, "fitness": None}
        record = self.db.get(parent_id)
        return {
            "parent_id": parent_id,
            "artifact_ref": record.artifact_ref if record else None,
            "fitness": record.fitness if record else None,
        }

    def _inspiration_refs(self, inspiration_ids: List[str]) -> Dict[str, Any]:
        """Return minimal public inspiration payloads in cached order."""
        inspirations = []
        for inspiration_id in inspiration_ids:
            record = self.db.get(inspiration_id)
            if record is None:
                continue
            inspirations.append(
                {
                    "inspiration_id": inspiration_id,
                    "artifact_ref": record.artifact_ref,
                    "fitness": record.fitness,
                }
            )
        return {"inspirations": inspirations}


def _load_verification_result(verification_result_ref: str) -> Dict[str, Any]:
    """Load verifier JSON from an absolute path or cwd-relative reference."""
    path = Path(verification_result_ref).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("verification_result_ref must point to a JSON object.")
    return data


def _extract_fitness(verification_result: Dict[str, Any]) -> Optional[float]:
    """Extract top-level numeric fitness from verifier output."""
    value = verification_result.get("fitness")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _record_response(
    entered_success_db: Optional[bool],
    success_record_id: Optional[str],
) -> Dict[str, Any]:
    """Return the minimal public attempt-recording payload."""
    entered = bool(entered_success_db)
    return {
        "entered_success_db": entered,
        "success_record_id": success_record_id if entered else None,
    }

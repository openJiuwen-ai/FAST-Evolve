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

from agent_evolve_mcp.budget_guard import RunBudgetGuard
from agent_evolve_mcp.config import load_evolution_controller_config
from agent_evolve_mcp.convergence_detector import FitnessPlateauDetector
from agent_evolve_mcp.inspiration_selector import LineageBoltzmannInspirationSelector
from agent_evolve_mcp.lineage_tree import LineageTree
from agent_evolve_mcp.models import (
    AttemptRecord,
    EvolutionControllerConfig,
    RunState,
    SearchState,
    SuccessRecord,
)
from agent_evolve_mcp.meta_guidance import MetaGuidanceManager
from agent_evolve_mcp.notes import NOTES_ENABLED_ENV, NotesManager, notes_enabled
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
        self.notes = NotesManager(run_id)
        self.meta_guidance = MetaGuidanceManager(run_id, self.config.meta_evolve)
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
        self.budget_guard = RunBudgetGuard(self.config.budget)
        self.plateau_detector = FitnessPlateauDetector(self.config.plateau)

    def configure_run(self, task_description: str = "") -> None:
        """Persist optional run metadata supplied during initial configuration."""
        run_state = self.run_state_store.load()
        if task_description and not run_state.task_description:
            run_state.task_description = task_description
            self.run_state_store.save(run_state)

    def select_parent(self, task_id: str) -> Dict[str, Any]:
        """Return the minimal parent ref for one generator task.

        The first call for a task samples and caches a parent.  Later retries
        return the cached parent without incrementing selection count again.
        """
        self._load_state()
        context = self.task_contexts.get(task_id)
        if context is not None and context.parent_selected:
            return self._parent_ref(context.parent_id)

        context = self.task_contexts.ensure(task_id)
        current_best = self.db.get_canonical_best_record()
        guidance = (
            self.meta_guidance.assign_for_task(
                task_id,
                anchor_parent_id=current_best.id,
                anchor_fitness=current_best.fitness,
            )
            if current_best is not None
            else None
        )
        if guidance is not None:
            parent = self.db.get(str(guidance.get("anchor_parent_id")))
            if parent is not None:
                context.meta_guidance_id = str(guidance.get("guidance_id"))
                context.anchor_parent_id = str(guidance.get("anchor_parent_id"))
            else:
                guidance = None

        if guidance is None:
            parent = self.parent_selector.select(
                self.db,
                self._progress(),
            )

        context.parent_id = parent.id if parent is not None else None
        context.parent_selected = True
        self.task_contexts.put(context)
        if guidance is not None and context.parent_id:
            self.db.increment_selection(context.parent_id)
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
            raise ValueError(
                "Call evolve_select_parent before evolve_select_inspirations for this task."
            )

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
        )
        context.inspiration_ids = [
            str(item["id"])
            for item in inspirations
            if not item.get("is_parent") and item.get("id") != parent.id
        ]
        context.inspirations_selected = True
        self.task_contexts.put(context)
        return self._inspiration_refs(context.inspiration_ids)

    def get_notes_prompt(self, task_id: str) -> Dict[str, Any]:
        """Return the run-local Notes prompt after parent/inspirations are fixed."""
        if not notes_enabled():
            return {
                "available": False,
                "prompt": "",
                "selected_note_ids": [],
                "counts": {
                    "error": 0,
                    "positive": 0,
                    "negative": 0,
                    "matched_pos_neg": 0,
                    "random_fill": 0,
                },
                "disabled": True,
                "reason": f"Run-local Notes disabled by {NOTES_ENABLED_ENV}.",
            }
        self._load_state()
        context = self.task_contexts.get(task_id)
        if context is None or not context.parent_selected:
            raise ValueError("Call evolve_select_parent before evolve_get_notes_prompt.")
        if not context.inspirations_selected:
            raise ValueError("Call evolve_select_inspirations before evolve_get_notes_prompt.")
        return self.notes.get_prompt(
            task_id=task_id,
            parent_id=context.parent_id,
            inspiration_ids=context.inspiration_ids,
        )

    def record_attempt(
        self,
        task_id: str,
        verification_result_ref: str,
        attempt_id: Optional[str] = None,
        artifact_ref: Optional[str] = None,
        max_attempts: int = 3,
    ) -> Dict[str, Any]:
        """Record one verifier result and return any run-local note requests.

        This is attempt-level, not task-level, because Notes needs failed
        attempts as evidence. Repeated calls for the same task/attempt pair are
        idempotent.
        """
        self._load_state()
        context = self.task_contexts.ensure(task_id)

        verification_result = _load_verification_result(verification_result_ref)
        resolved_artifact_ref = str(artifact_ref or verification_result.get("artifact_ref") or "")
        if not resolved_artifact_ref:
            raise ValueError(
                "artifact_ref must be supplied or present in verification_result_ref JSON."
            )

        resolved_attempt_id = str(
            attempt_id
            or verification_result.get("attempt_id")
            or _infer_attempt_id(verification_result_ref)
            or task_id
            or uuid4()
        )
        existing_attempt = self.attempt_log.find_by_task_attempt(task_id, resolved_attempt_id)
        if existing_attempt is not None:
            existing_ref = existing_attempt.get("verification_result_ref")
            if existing_ref and existing_ref != verification_result_ref:
                raise ValueError(
                    "Attempt log already contains this task/attempt with a different verification_result_ref."
                )
            note_requests = (
                self.notes.public_requests_for_attempt(task_id, resolved_attempt_id)
                if notes_enabled()
                else []
            )
            return _record_response_from_attempt(
                existing_attempt,
                note_requests=note_requests,
                relative_delta_to_parent=_relative_delta_to_parent(existing_attempt, self.db),
            )

        subagent_id = str(verification_result.get("subagent_id") or "")
        parent_id = context.parent_id if context.parent_selected else None
        inspiration_ids = list(context.inspiration_ids)
        generation = self.db.infer_next_generation(parent_id)

        status = str(verification_result.get("status", "")).lower()
        fitness = _extract_fitness(verification_result)
        verification_metrics = dict(verification_result.get("metrics") or {})
        best_before = _best_fitness(self.db)
        entered_success_db = False
        success_record_id = None

        if status == "success" and fitness is not None:
            success_record_id = task_id
            if self.db.get(success_record_id) is not None:
                entered_success_db = True
            else:
                metrics = verification_metrics
                profile = dict(verification_result.get("profile") or {})
                if not profile:
                    profile = metrics
                record = SuccessRecord(
                    id=success_record_id,
                    run_id=self.run_id,
                    fitness=fitness,
                    parent_id=parent_id,
                    generation=generation,
                    artifact_ref=resolved_artifact_ref,
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
                        "attempt_id": resolved_attempt_id,
                        "inspiration_ids": inspiration_ids,
                        "evaluator_version": verification_result.get("evaluator_version"),
                        "log_ref": verification_result.get("log_ref"),
                        "summary_ref": verification_result.get("summary_ref"),
                        "report_ref": verification_result.get("report_ref"),
                    },
                )
                entered_success_db = self.db.add(record)

        attempt = AttemptRecord(
            id=resolved_attempt_id,
            run_id=self.run_id,
            task_id=task_id,
            subagent_id=subagent_id,
            artifact_ref=resolved_artifact_ref,
            parent_id=parent_id,
            inspiration_ids=inspiration_ids,
            generation=generation,
            verification_result=verification_result,
            meta_guidance_id=context.meta_guidance_id,
            anchor_parent_id=context.anchor_parent_id,
            verification_result_ref=verification_result_ref,
            sketch=str(verification_result.get("summary") or ""),
            entered_success_db=entered_success_db,
            success_record_id=success_record_id if entered_success_db else None,
        )
        self.attempt_log.append(attempt)

        if status == "success" or _attempt_number(resolved_attempt_id) >= max_attempts:
            context.recorded = True
            context.verification_result_ref = verification_result_ref
            context.entered_success_db = entered_success_db
            context.success_record_id = success_record_id if entered_success_db else None
            self.task_contexts.put(context)

        self.db.load()
        best_after = _best_fitness(self.db)
        final_task_attempt = (
            status == "success" or _attempt_number(resolved_attempt_id) >= max_attempts
        )
        if final_task_attempt:
            anchor_record = (
                self.db.get(context.anchor_parent_id) if context.anchor_parent_id else None
            )
            self.meta_guidance.record_attempt_outcome(
                task_id=task_id,
                attempt_id=resolved_attempt_id,
                valid_result=status == "success" and fitness is not None,
                entered_success_db=entered_success_db,
                success_record_id=success_record_id if entered_success_db else None,
                fitness=fitness,
                anchor_fitness=anchor_record.fitness if anchor_record is not None else None,
                metrics=verification_metrics,
                anchor_metrics=(
                    dict(anchor_record.metrics or {}) if anchor_record is not None else {}
                ),
                best_fitness_before=best_before,
                best_fitness_after=best_after,
            )
        if notes_enabled():
            all_attempts = self.attempt_log.get_all()
            note_requests = self.notes.build_note_requests(
                attempt=attempt,
                all_attempt_rows=all_attempts,
                db=self.db,
                max_attempts=max_attempts,
            )
        else:
            note_requests = []
        relative_delta = _relative_delta_to_parent(attempt.to_dict(), self.db)
        return _record_response_from_attempt(
            attempt.to_dict(),
            note_requests=note_requests,
            relative_delta_to_parent=relative_delta,
        )

    def commit_generated_note(
        self, note_request_id: str, generated_note_json: Any
    ) -> Dict[str, Any]:
        """Commit one LLM-distilled run-local note."""
        if not notes_enabled():
            return {
                "committed": False,
                "disabled": True,
                "reason": f"Run-local Notes disabled by {NOTES_ENABLED_ENV}.",
            }
        return self.notes.commit_generated_note(note_request_id, generated_note_json)

    def check_convergence(self) -> Dict[str, Any]:
        """Return the next scheduler action from convergence/meta-evolve state."""
        self._load_state()
        run_state = self.run_state_store.load()
        total_tasks = self._task_count()
        total_attempts = self.attempt_log.count()
        total_success = len(self.db.get_all())
        elapsed_seconds = self.run_state_store.elapsed_seconds()
        reason = self.budget_guard.exhaustion_reason(
            total_tasks=total_tasks,
            elapsed_seconds=elapsed_seconds,
        )
        budget_exhausted = bool(reason)
        if budget_exhausted:
            self.plateau_detector.mark_stopped(reason, self.db, self.lineage_tree)
        else:
            self.plateau_detector.evaluate(
                self.db,
                self.lineage_tree,
                total_success=total_success,
            )

        diagnostics = self.plateau_detector.get_diagnostics()
        action = "continue"
        request_id = None

        if budget_exhausted:
            action = "budget_exhausted"
        elif self.plateau_detector.state == SearchState.WATCHING:
            current_best = diagnostics.get("current_best_fitness")
            self.meta_guidance.note_watching(
                task_count=total_tasks,
                current_best_fitness=current_best,
            )
            if self.meta_guidance.should_request_guidance(
                current_best_fitness=current_best,
            ):
                request = self.meta_guidance.ensure_pending_request(
                    db=self.db,
                    task_description=run_state.task_description,
                    diagnostics=diagnostics,
                    lineages=self._lineage_summaries(),
                    notes_summary=(
                        self.notes.summarize_for_meta_guidance()
                        if notes_enabled()
                        else {
                            "enabled": False,
                            "reason": f"Run-local Notes disabled by {NOTES_ENABLED_ENV}.",
                            "positive": [],
                            "negative": [],
                            "error": [],
                            "errors": [],
                        }
                    ),
                    task_count=total_tasks,
                )
                if request is not None:
                    action = "request_meta_guidance"
                    request_id = request.get("request_id")
        else:
            self.meta_guidance.reset_watching()

        meta_stats = self.meta_guidance.stats()
        diagnostics["meta_guidance"] = meta_stats
        self.run_state_store.save(
            RunState(
                run_id=self.run_id,
                started_at=run_state.started_at,
                task_description=run_state.task_description,
                search_state=self.plateau_detector.state.value,
                stop_reason=reason,
                diagnostics=diagnostics,
                last_checked_attempt_count=total_attempts,
            )
        )
        response: Dict[str, Any] = {
            "action": action,
            "search_state": self.plateau_detector.state.value,
            "plateau": self.plateau_detector.state == SearchState.WATCHING,
            "stop_reason": reason,
            "meta_guidance": meta_stats,
            "diagnostics": diagnostics,
        }
        if request_id:
            response["request_id"] = request_id
        return response

    def get_meta_guidance_request(self, request_id: str = "") -> Dict[str, Any]:
        """Return a pending meta-guidance request."""
        return self.meta_guidance.get_request(request_id or None)

    def commit_meta_guidance(self, request_id: str, guidance_json: Any) -> Dict[str, Any]:
        """Validate and activate host-generated meta-guidance."""
        return self.meta_guidance.commit_guidance(request_id, guidance_json)

    def get_meta_guidance_prompt(self, task_id: str) -> Dict[str, Any]:
        """Return the meta-guidance prompt assigned during parent selection."""
        self._load_state()
        context = self.task_contexts.get(task_id)
        if context is None or not context.parent_selected:
            raise ValueError("Call evolve_select_parent before evolve_get_meta_guidance_prompt.")
        return self.meta_guidance.prompt_for_task(task_id)

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
            task_description=run_state.task_description,
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
        self.plateau_detector.restore_state(
            run_state.search_state,
            stop_reason=run_state.stop_reason,
            diagnostics=run_state.diagnostics,
        )
        self.lineage_tree.rebuild(self.db)

    def _progress(self) -> float:
        """Compute search progress internally from registered tasks."""
        max_total = max(self.config.budget.max_total_tasks, 1)
        return min(self._task_count() / max_total, 1.0)

    def _task_count(self) -> int:
        """Return the number of unique tasks registered in this run.

        AttemptLog intentionally retains every retry for Notes and diagnostics,
        so its row count must not be used as the task-budget counter.
        """
        attempt_task_ids = {
            str(row["task_id"]) for row in self.attempt_log.get_all() if row.get("task_id")
        }
        return max(self.task_contexts.count(), len(attempt_task_ids))

    def _parent_ref(self, parent_id: Optional[str]) -> Dict[str, Any]:
        """Return the compact public parent payload, including diagnostics."""
        if parent_id is None:
            return {
                "parent_id": None,
                "artifact_ref": None,
                "fitness": None,
                "metrics": {},
            }
        record = self.db.get(parent_id)
        return {
            "parent_id": parent_id,
            "artifact_ref": record.artifact_ref if record else None,
            "fitness": record.fitness if record else None,
            "metrics": dict(record.metrics or {}) if record else {},
        }

    def _inspiration_refs(self, inspiration_ids: List[str]) -> Dict[str, Any]:
        """Return compact public inspiration payloads in cached order."""
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
                    "metrics": dict(record.metrics or {}),
                }
            )
        return {"inspirations": inspirations}

    def _lineage_summaries(self) -> List[Dict[str, Any]]:
        """Return compact lineage summaries for meta-guidance requests."""
        summaries: List[Dict[str, Any]] = []
        for root_id, stats in sorted(self.lineage_tree.get_all_lineage_stats().items()):
            summaries.append(
                {
                    "root_id": root_id,
                    "size": stats.size,
                    "best_fitness": stats.best_fitness,
                    "best_record_id": stats.best_record_id,
                    "max_depth": stats.max_depth,
                    "share": stats.share,
                    "recent_improvement": stats.recent_improvement,
                }
            )
        return summaries


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


def _best_fitness(db: SuccessDB) -> Optional[float]:
    best = db.get_canonical_best_record()
    return best.fitness if best else None


def _infer_attempt_id(verification_result_ref: str) -> Optional[str]:
    """Infer attempt id from a verifier path like .../attempt_001/eval_result.json."""
    try:
        parent_name = Path(verification_result_ref).parent.name
    except Exception:
        return None
    if parent_name.startswith("attempt_"):
        return parent_name
    return None


def _attempt_number(attempt_id: str) -> int:
    text = str(attempt_id)
    if "_" in text:
        text = text.rsplit("_", 1)[-1]
    try:
        return int(text)
    except ValueError:
        return 1


def _relative_delta_to_parent(row: Dict[str, Any], db: SuccessDB) -> Optional[float]:
    parent_id = row.get("parent_id")
    parent = db.get(str(parent_id)) if parent_id else None
    if parent is None:
        return None
    verification_result = dict(row.get("verification_result") or {})
    fitness = _extract_fitness(verification_result)
    if fitness is None:
        return None
    denom = max(abs(parent.fitness), 1e-6)
    return (fitness - parent.fitness) / denom


def _record_response_from_attempt(
    row: Dict[str, Any],
    *,
    note_requests: List[Dict[str, Any]],
    relative_delta_to_parent: Optional[float],
) -> Dict[str, Any]:
    """Return the public attempt-recording payload for Notes."""
    entered = bool(row.get("entered_success_db"))
    verification_result = dict(row.get("verification_result") or {})
    return {
        "recorded": True,
        "attempt_id": row.get("id"),
        "entered_success_db": entered,
        "success_record_id": row.get("success_record_id") if entered else None,
        "fitness": _extract_fitness(verification_result),
        "relative_delta_to_parent": relative_delta_to_parent,
        "note_requests": note_requests,
    }

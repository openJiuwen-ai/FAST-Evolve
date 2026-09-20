"""Internal facade for the Evolution Controller MCP.

The public tools expose a deliberately small schema.  This controller owns the
larger local state needed to make that possible: SuccessPool, attempt log, task
context cache, run state, lineage indexes, and the three strategy solvers.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from evotrias import scheduler

from evotrias.db.config import Config
from evotrias.logger import logger
from evotrias.db.attempts import AttemptRecord, Attempts
from evotrias.db.run_state import RunState
from evotrias.db.task_contexts import TaskContext, TaskContexts
from evotrias.enums import SearchState
from evotrias.evolve_ctrl.budget_guard import RunBudgetGuard
from evotrias.evolve_ctrl.convergence_detector import FitnessPlateauDetector
from evotrias.evolve_ctrl.inspiration_selector import LineageBoltzmannInspirationSelector
from evotrias.evolve_ctrl.lineage_tree import LineageTree
from evotrias.evolve_ctrl.meta_guidance import MetaGuidanceManager
from evotrias.evolve_ctrl.notes import NotesManager
from evotrias.evolve_ctrl.parent_selector import LineageAwareParentSelector
from evotrias.evolve_ctrl.success_pool import SuccessPool
from evotrias.mcp.run_report import write_run_report


META_EVOLVE_ENABLED_ENV = "AGENT_EVOLVE_META_EVOLVE_ENABLED"
PERF_IMPROVEMENT_THRESHOLD_ENV = "AGENT_EVOLVE_PERF_IMPROVEMENT_THRESHOLD"
NOTES_ENABLED_ENV = "AGENT_EVOLVE_NOTES_ENABLED"


def configure_run(
    run_id: str,
    *,
    max_total_tasks: int = 50,
    lineage_root_number: int = 4,
    max_concurrent_tasks: int = 4,
) -> None:
    """Write ``config.json`` for a new run.

    Environment overrides are resolved here, once, so the persisted file is
    the complete configuration for the rest of the run.
    """
    with Config(run_id) as config:
        config.budget.max_total_tasks = int(max_total_tasks)
        config.scheduler.lineage_root_number = max(int(lineage_root_number), 1)
        config.scheduler.max_concurrent_tasks = max(int(max_concurrent_tasks), 1)
        config.meta_evolve.enabled = _bool_from_env(META_EVOLVE_ENABLED_ENV, config.meta_evolve.enabled)
        config.plateau.improvement_threshold = _float_from_env(
            PERF_IMPROVEMENT_THRESHOLD_ENV, config.plateau.improvement_threshold
        )
        config.notes.enabled = _bool_from_env(NOTES_ENABLED_ENV, config.notes.enabled)


def _bool_from_env(env_name: str, default: bool) -> bool:
    value = os.getenv(env_name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _float_from_env(env_name: str, default: float) -> float:
    value = os.getenv(env_name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


class EvolutionController:
    """Coordinate persistence, lineage indexing, selectors, and convergence."""

    def __init__(self, run_id: str):
        """Create a controller bound to one run id.

        The controller object itself is only a convenience cache.  Every public
        operation reloads disk-backed state before making a decision.  Run
        configuration comes from ``config.json`` written by ``configure_run``;
        a run that was never configured gets defaults.
        """
        self.run_id = run_id
        self.config = Config(run_id)
        if not self.config.created_at:
            logger.warning(f"run {run_id}: no config.json yet, controller is using defaults")
        self.success_pool = SuccessPool(run_id)
        self.attempt_log = Attempts(run_id)
        self.notes = NotesManager(run_id, self.config.notes)
        self.meta_guidance = MetaGuidanceManager(run_id, self.config.meta_evolve)
        self.lineage_tree = LineageTree()
        self.lineage_tree.rebuild(self.success_pool)
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
        """Create run_state.json (its created_at is the run's start) and record the task description."""
        with RunState(self.run_id) as run_state:
            if task_description and not run_state.task_description:
                run_state.task_description = task_description

    def select_parent(self, task_id: str) -> Dict[str, Any]:
        """Return the minimal parent ref for one generator task.

        The first call for a task samples and caches a parent.  Later retries
        return the cached parent without incrementing selection count again.
        """
        self._load_state()
        existing = TaskContexts(self.run_id).get(task_id)
        if existing is not None and existing.parent_selected:
            return self._parent_ref(existing.parent_id)

        with TaskContexts(self.run_id) as contexts:
            context = contexts.get(task_id, create=True)
            if self._in_root_phase():
                # Only root tasks run before all roots have finished (evolution
                # tasks are minted strictly after that), so this task is a root:
                # it explores its own direction from scratch. Return no parent
                # (select_inspirations then returns none) even when early roots
                # already populated SuccessPool, and skip meta-guidance.
                context.parent_id = None
                context.parent_selected = True
                return self._parent_ref(None)

            current_best = self.success_pool.get_canonical_best_record()
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
                parent = self.success_pool.get(str(guidance.get("anchor_parent_id")))
                if parent is not None:
                    context.meta_guidance_id = str(guidance.get("guidance_id"))
                    context.anchor_parent_id = str(guidance.get("anchor_parent_id"))
                else:
                    guidance = None

            if guidance is None:
                parent = self.parent_selector.select(
                    self.success_pool,
                    self._progress(),
                )

            context.parent_id = parent.id if parent is not None else None
            context.parent_selected = True
        return self._parent_ref(context.parent_id)

    def select_inspirations(self, task_id: str) -> Dict[str, Any]:
        """Return minimal inspiration refs for one generator task.

        The public response intentionally excludes the parent.  The parent was
        already returned by ``select_parent`` and is stored in task context for
        later attempt recording.
        """
        self._load_state()
        existing = TaskContexts(self.run_id).get(task_id)
        if existing is None or not existing.parent_selected:
            raise ValueError(
                "Call select_parent before select_inspirations for this task."
            )
        if existing.inspirations_selected:
            return self._inspiration_refs(existing.inspiration_ids)

        with TaskContexts(self.run_id) as contexts:
            context = contexts.get(task_id, create=True)
            if context.parent_id is None:
                context.inspiration_ids = []
                context.inspirations_selected = True
                return {"inspirations": []}

            parent = self.success_pool.get(context.parent_id)
            if parent is None:
                raise ValueError(f"Cached parent {context.parent_id!r} is not present in SuccessPool.")

            inspirations = self.inspiration_selector.select(
                parent,
                self.success_pool,
                self._progress(),
            )
            context.inspiration_ids = [
                str(item["id"])
                for item in inspirations
                if not item.get("is_parent") and item.get("id") != parent.id
            ]
            context.inspirations_selected = True
        return self._inspiration_refs(context.inspiration_ids)

    def get_notes_prompt(self, task_id: str) -> Dict[str, Any]:
        """Return the run-local Notes prompt after parent/inspirations are fixed."""
        if not self.config.notes.enabled:
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
                "reason": "Run-local Notes disabled in run config.",
            }
        self._load_state()
        context = TaskContexts(self.run_id).get(task_id)
        if context is None or not context.parent_selected:
            raise ValueError("Call select_parent before get_notes_prompt.")
        if not context.inspirations_selected:
            raise ValueError("Call select_inspirations before get_notes_prompt.")
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
        context = TaskContexts(self.run_id).get(task_id) or TaskContext(task_id=task_id)

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
            existing_ref = existing_attempt.verification_result_ref
            if existing_ref and existing_ref != verification_result_ref:
                raise ValueError(
                    "Attempt log already contains this task/attempt with a different verification_result_ref."
                )
            note_requests = (
                self.notes.public_requests_for_attempt(task_id, resolved_attempt_id)
                if self.config.notes.enabled
                else []
            )
            existing_row = asdict(existing_attempt)
            return _record_response_from_attempt(
                existing_row,
                note_requests=note_requests,
                relative_delta_to_parent=_relative_delta_to_parent(existing_row, self.success_pool),
            )

        subagent_id = str(verification_result.get("subagent_id") or "")
        parent_id = context.parent_id if context.parent_selected else None
        inspiration_ids = list(context.inspiration_ids)
        generation = self.success_pool.infer_next_generation(parent_id)

        status = str(verification_result.get("status", "")).lower()
        fitness = _extract_fitness(verification_result)
        verification_metrics = dict(verification_result.get("metrics") or {})
        best_before = _best_fitness(self.success_pool)
        in_success_pool = False
        success_record_id = None

        if status == "success" and fitness is not None:
            success_record_id = task_id
            if task_id in self.success_pool:
                # The pool keeps one record per task; a later success for the
                # same task is logged but does not enter the pool again.
                logger.warning(
                    f"run {self.run_id}: task {task_id} succeeded again on attempt "
                    f"{resolved_attempt_id} after already entering the success pool"
                )
            else:
                in_success_pool = True

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
            in_success_pool=in_success_pool,
            success_record_id=success_record_id if in_success_pool else None,
        )
        self.attempt_log.append(attempt)

        if status == "success" or _attempt_number(resolved_attempt_id) >= max_attempts:
            with TaskContexts(self.run_id) as contexts:
                saved = contexts.get(task_id, create=True)
                saved.recorded = True
                saved.verification_result_ref = verification_result_ref
                saved.in_success_pool = in_success_pool
                saved.success_record_id = success_record_id if in_success_pool else None

        self.success_pool = SuccessPool(self.run_id)
        best_after = _best_fitness(self.success_pool)
        final_task_attempt = (
            status == "success" or _attempt_number(resolved_attempt_id) >= max_attempts
        )
        if final_task_attempt:
            anchor_record = (
                self.success_pool.get(context.anchor_parent_id) if context.anchor_parent_id else None
            )
            self.meta_guidance.record_attempt_outcome(
                task_id=task_id,
                attempt_id=resolved_attempt_id,
                valid_result=status == "success" and fitness is not None,
                in_success_pool=in_success_pool,
                success_record_id=success_record_id if in_success_pool else None,
                fitness=fitness,
                anchor_fitness=anchor_record.fitness if anchor_record is not None else None,
                metrics=verification_metrics,
                anchor_metrics=(
                    dict(anchor_record.metrics or {}) if anchor_record is not None else {}
                ),
                best_fitness_before=best_before,
                best_fitness_after=best_after,
            )
        if self.config.notes.enabled:
            note_requests = self.notes.build_note_requests(
                attempt=attempt,
                attempts=self.attempt_log,
                db=self.success_pool,
                max_attempts=max_attempts,
            )
        else:
            note_requests = []
        relative_delta = _relative_delta_to_parent(asdict(attempt), self.success_pool)
        return _record_response_from_attempt(
            asdict(attempt),
            note_requests=note_requests,
            relative_delta_to_parent=relative_delta,
        )

    def commit_generated_note(
        self, note_request_id: str, generated_note_json: Any
    ) -> Dict[str, Any]:
        """Commit one LLM-distilled run-local note."""
        if not self.config.notes.enabled:
            return {
                "committed": False,
                "disabled": True,
                "reason": "Run-local Notes disabled in run config.",
            }
        return self.notes.commit_generated_note(note_request_id, generated_note_json)

    def check_convergence(
        self,
        concurrency: int,
    ) -> Dict[str, Any]:
        """Return the next scheduler action from convergence/meta-evolve state.

        Besides convergence/meta-guidance checking, this call is the run's
        only task faucet: on ``action == "continue"`` it mints new task ids
        into the pool and reports the count as ``tasks_to_create``.

        Background: a run first explores ``lineage_root_number`` independent
        directions, one root task per direction; these roots become the
        lineage roots of the search.  Every root must finish before any
        evolution (parent-based) task is created, so no initial direction is
        starved and parent selection can draw from the complete set of
        established lineages.  ``concurrency`` — required, positive, normally
        the run's confirmed ``max_concurrent_tasks`` — caps in-flight tasks
        in every phase.

        Task creation therefore moves through three phases:

        1. Root phase (``created < lineage_root_number``): top up in-flight
           tasks toward ``concurrency``, but never mint more than
           ``lineage_root_number`` tasks in total.
        2. Root barrier (``created == lineage_root_number`` with unfinished
           tasks): mint nothing — ``action`` stays ``"continue"`` with
           ``tasks_to_create`` 0 until every root has finished.  This is a
           normal waiting state, not a stop signal.
        3. Evolution phase (all roots finished): top up in-flight tasks to
           ``concurrency`` after each completion.

        Every phase is additionally capped by the ``max_total_tasks`` budget.
        """
        if concurrency < 1:
            raise ValueError(
                f"concurrency must be a positive integer, got {concurrency!r}"
            )
        self._load_state()
        run_state = RunState(self.run_id)
        total_tasks = self._task_count()
        total_attempts = len(self.attempt_log)
        elapsed_seconds = run_state.elapsed_seconds()
        budget_reason = self.budget_guard.exhaustion_reason(
            total_tasks=total_tasks,
            elapsed_seconds=elapsed_seconds,
        )
        budget_exhausted = bool(budget_reason)
        state, reason, diagnostics = self.plateau_detector.evaluate(
            run_state,
            self.success_pool,
            self.lineage_tree,
            stop_reason=budget_reason,
        )
        action = "continue"
        request_id = None

        if budget_exhausted:
            action = "budget_exhausted"
        elif state == SearchState.WATCHING:
            current_best = diagnostics.get("current_best_fitness")
            self.meta_guidance.note_watching(
                task_count=total_tasks,
                current_best_fitness=current_best,
            )
            if self.meta_guidance.should_request_guidance(
                current_best_fitness=current_best,
            ):
                request = self.meta_guidance.ensure_pending_request(
                    db=self.success_pool,
                    task_description=run_state.task_description,
                    diagnostics=diagnostics,
                    lineages=self._lineage_summaries(),
                    notes_summary=(
                        self.notes.summarize_for_meta_guidance()
                        if self.config.notes.enabled
                        else {
                            "enabled": False,
                            "reason": "Run-local Notes disabled in run config.",
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

        tasks_to_create = 0
        if action == "continue":
            root_number = self.config.scheduler.lineage_root_number
            max_total = self.config.budget.max_total_tasks
            # During the root phase minting stays capped at root_number: that
            # both limits the phase to one task per direction and makes
            # top_up_tasks return [] during the root barrier (all roots
            # minted, some still in flight).
            mint_cap = min(root_number, max_total) if self._in_root_phase() else max_total
            newly_created = scheduler.top_up_tasks(self.run_id, concurrency, mint_cap)
            tasks_to_create = len(newly_created)

        meta_stats = self.meta_guidance.stats()
        diagnostics["meta_guidance"] = meta_stats
        if state != run_state.search_state:
            logger.info(
                f"run {self.run_id}: search state {run_state.search_state.value} -> {state.value}"
                + (f" ({reason})" if reason else "")
            )
        with RunState(self.run_id) as saved:
            saved.search_state = state
            saved.stop_reason = reason
            saved.diagnostics = diagnostics
            saved.last_checked_attempt_count = total_attempts
        response: Dict[str, Any] = {
            "action": action,
            "tasks_to_create": tasks_to_create,
            "search_state": state.value,
            "plateau": state == SearchState.WATCHING,
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
        """Validate and activate worker-generated meta-guidance."""
        return self.meta_guidance.commit_guidance(request_id, guidance_json)

    def get_meta_guidance_prompt(self, task_id: str) -> Dict[str, Any]:
        """Return the meta-guidance prompt assigned during parent selection."""
        self._load_state()
        context = TaskContexts(self.run_id).get(task_id)
        if context is None or not context.parent_selected:
            raise ValueError("Call select_parent before get_meta_guidance_prompt.")
        return self.meta_guidance.prompt_for_task(task_id)

    def finalize_run(self, stop_reason: Optional[str] = None) -> Dict[str, Any]:
        """Write deterministic run summary artifacts and return compact refs.

        Finalization is intentionally idempotent.  Repeated calls regenerate the
        same report files from current persisted state without changing
        SuccessPool or attempt history.
        """
        self._load_state()
        with RunState(self.run_id) as run_state:
            run_state.search_state = SearchState.STOPPED
            run_state.stop_reason = stop_reason or run_state.stop_reason or "manual_finalize"
            run_state.last_checked_attempt_count = len(self.attempt_log)
        self._load_state()
        return write_run_report(
            run_id=self.run_id,
            db=self.success_pool,
            lineage_tree=self.lineage_tree,
            run_state=RunState(self.run_id),
        )

    def _load_state(self) -> None:
        """Reload all disk-backed state needed for a decision."""
        self.success_pool = SuccessPool(self.run_id)
        self.lineage_tree.rebuild(self.success_pool)

    def _in_root_phase(self) -> bool:
        """True until all ``lineage_root_number`` roots have finished.

        Completed and failed pool tasks both count as finished. The count is
        monotonic and evolution tasks are only minted after it reaches the
        root number, so this flips exactly once per run.
        """
        pool = scheduler.pool_snapshot(self.run_id)
        finished = len(pool.completed) + len(pool.failed)
        return finished < self.config.scheduler.lineage_root_number

    def _progress(self) -> float:
        """Compute search progress internally from registered tasks."""
        max_total = max(self.config.budget.max_total_tasks, 1)
        return min(self._task_count() / max_total, 1.0)

    def _task_count(self) -> int:
        """Return the number of unique tasks registered in this run.

        Attempts intentionally retains every retry for Notes and diagnostics,
        so its row count must not be used as the task-budget counter.
        """
        attempt_task_ids = {a.task_id for a in self.attempt_log if a.task_id}
        return max(len(TaskContexts(self.run_id)), len(attempt_task_ids))

    def _parent_ref(self, parent_id: Optional[str]) -> Dict[str, Any]:
        """Return the compact public parent payload, including diagnostics."""
        if parent_id is None:
            return {
                "parent_id": None,
                "artifact_ref": None,
                "fitness": None,
                "metrics": {},
            }
        record = self.success_pool.get(parent_id)
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
            record = self.success_pool.get(inspiration_id)
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


def _best_fitness(success_pool: SuccessPool) -> Optional[float]:
    best = success_pool.get_canonical_best_record()
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


def _relative_delta_to_parent(row: Dict[str, Any], success_pool: SuccessPool) -> Optional[float]:
    parent_id = row.get("parent_id")
    parent = success_pool.get(str(parent_id)) if parent_id else None
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
    entered = bool(row.get("in_success_pool"))
    verification_result = dict(row.get("verification_result") or {})
    return {
        "recorded": True,
        "attempt_id": row.get("id"),
        "in_success_pool": entered,
        "success_record_id": row.get("success_record_id") if entered else None,
        "fitness": _extract_fitness(verification_result),
        "relative_delta_to_parent": relative_delta_to_parent,
        "note_requests": note_requests,
    }

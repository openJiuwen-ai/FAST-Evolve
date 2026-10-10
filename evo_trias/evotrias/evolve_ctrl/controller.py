"""Evolution control entry points, one function per MCP tool.

Nothing here is cached between calls: every function takes ``run_id``, reads
what it needs from ``evotrias.db``, runs the algorithm classes from this
package, and writes results back through ``with`` blocks.  The MCP layer
fills in every argument (defaults live there, as the agent-facing contract)
and forwards; it never touches db objects itself.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from evotrias import scheduler
from evotrias.db.attempts import AttemptRecord, Attempts
from evotrias.db.config import Config
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
from evotrias.evolve_ctrl.run_report import write_run_report
from evotrias.evolve_ctrl.success_pool import SuccessPool
from evotrias.logger import logger

META_EVOLVE_ENABLED_ENV = "EVOTRIAS_META_EVOLVE_ENABLED"
PERF_IMPROVEMENT_THRESHOLD_ENV = "EVOTRIAS_PERF_IMPROVEMENT_THRESHOLD"
NOTES_ENABLED_ENV = "EVOTRIAS_NOTES_ENABLED"


def configure_run(
    run_id: str,
    task_info: Dict[str, Any],
    configs: Dict[str, Any],
) -> Tuple[bool, Config, str]:
    """Write ``config.json`` and ``run_state.json`` once for a new run.

    Returns ``(configured, config, message)``.  ``configured`` is False when the
    run already has a config; ``config`` is then the locked on-disk value.
    Environment overrides are resolved here, once, so the persisted file is
    the complete configuration for the rest of the run.
    """
    if Config(run_id).created_at:
        return False, Config(run_id), "Run already configured for this run_id; configuration locked."
    with Config(run_id) as config:
        config.budget.max_total_tasks = int(configs["max_total_tasks"])
        config.scheduler.lineage_root_number = max(int(configs["lineage_root_number"]), 1)
        config.scheduler.max_concurrent_tasks = max(int(configs["max_concurrent_tasks"]), 1)
        config.meta_evolve.enabled = _bool_from_env(META_EVOLVE_ENABLED_ENV, config.meta_evolve.enabled)
        config.plateau.improvement_threshold = _float_from_env(
            PERF_IMPROVEMENT_THRESHOLD_ENV, config.plateau.improvement_threshold
        )
        config.notes.enabled = _bool_from_env(NOTES_ENABLED_ENV, config.notes.enabled)
    with RunState(run_id) as run_state:
        run_state.task_description = str(task_info.get("task_description") or "")
    logger.info(f"run {run_id}: configured {configs}")
    return True, config, "Run configured."


def select_parent(run_id: str, task_id: str) -> Dict[str, Any]:
    """Return the minimal parent ref for one generator task.

    The first call for a task samples and caches a parent.  Later retries
    return the cached parent without sampling again.
    """
    config = _config(run_id)
    pool = SuccessPool(run_id)
    existing = TaskContexts(run_id).get(task_id)
    if existing is not None and existing.parent_selected:
        return _parent_ref(pool, existing.parent_id)

    with TaskContexts(run_id) as contexts:
        context = contexts.get(task_id, create=True)
        if _in_root_phase(run_id, config):
            # Only root tasks run before all roots have finished (evolution
            # tasks are minted strictly after that), so this task is a root:
            # it explores its own direction from scratch. Return no parent
            # (select_inspirations then returns none) even when early roots
            # already populated SuccessPool, and skip meta-guidance.
            context.parent_id = None
            context.parent_selected = True
            logger.info(f"run {run_id}: task {task_id} is a root, no parent")
            return _parent_ref(pool, None)

        meta_guidance = MetaGuidanceManager(run_id, config.meta_evolve)
        current_best = pool.get_canonical_best_record()
        guidance = (
            meta_guidance.assign_for_task(
                task_id,
                anchor_parent_id=current_best.id,
                anchor_fitness=current_best.fitness,
            )
            if current_best is not None
            else None
        )
        parent = None
        if guidance is not None:
            parent = pool.get(guidance.anchor_parent_id)
            if parent is not None:
                context.meta_guidance_id = guidance.guidance_id
                context.anchor_parent_id = guidance.anchor_parent_id
            else:
                guidance = None

        if guidance is None:
            selector = LineageAwareParentSelector(config.parent_selection, _lineage(pool))
            parent = selector.select(pool, _progress(run_id, config))

        context.parent_id = parent.id if parent is not None else None
        context.parent_selected = True
        source = f"guidance {context.meta_guidance_id}" if guidance is not None else "selector"
        logger.info(f"run {run_id}: task {task_id} parent {context.parent_id!r} via {source}")
    return _parent_ref(pool, context.parent_id)


def select_inspirations(run_id: str, task_id: str) -> Dict[str, Any]:
    """Return minimal inspiration refs for one generator task.

    The public response intentionally excludes the parent.  The parent was
    already returned by ``select_parent`` and is stored in task context for
    later attempt recording.
    """
    config = _config(run_id)
    pool = SuccessPool(run_id)
    existing = TaskContexts(run_id).get(task_id)
    if existing is None or not existing.parent_selected:
        raise ValueError(
            "Call select_parent before select_inspirations for this task."
        )
    if existing.inspirations_selected:
        return _inspiration_refs(pool, existing.inspiration_ids)

    with TaskContexts(run_id) as contexts:
        context = contexts.get(task_id, create=True)
        if context.parent_id is None:
            context.inspiration_ids = []
            context.inspirations_selected = True
            return {"inspirations": []}

        parent = pool.get(context.parent_id)
        if parent is None:
            raise ValueError(f"Cached parent {context.parent_id!r} is not present in SuccessPool.")

        selector = LineageBoltzmannInspirationSelector(config.inspiration, _lineage(pool))
        inspirations = selector.select(parent, pool, _progress(run_id, config))
        context.inspiration_ids = [
            str(item["id"])
            for item in inspirations
            if not item.get("is_parent") and item.get("id") != parent.id
        ]
        context.inspirations_selected = True
        logger.info(
            f"run {run_id}: task {task_id} inspirations "
            f"{len(context.inspiration_ids)} {context.inspiration_ids}"
        )
    return _inspiration_refs(pool, context.inspiration_ids)


def get_notes_prompt(run_id: str, task_id: str) -> Dict[str, Any]:
    """Return the run-local Notes prompt after parent/inspirations are fixed."""
    config = _config(run_id)
    if not config.notes.enabled:
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
    context = TaskContexts(run_id).get(task_id)
    if context is None or not context.parent_selected:
        raise ValueError("Call select_parent before get_notes_prompt.")
    if not context.inspirations_selected:
        raise ValueError("Call select_inspirations before get_notes_prompt.")
    return NotesManager(run_id, config.notes).get_prompt(
        task_id=task_id,
        parent_id=context.parent_id,
        inspiration_ids=context.inspiration_ids,
    )


def record_attempt(
    run_id: str,
    task_id: str,
    verification_result_ref: str,
    attempt_id: Optional[str],
    artifact_ref: Optional[str],
    max_attempts: int,
) -> Dict[str, Any]:
    """Record one verifier result and return any run-local note requests.

    This is attempt-level, not task-level, because Notes needs failed
    attempts as evidence. Repeated calls for the same task/attempt pair are
    idempotent.
    """
    config = _config(run_id)
    pool = SuccessPool(run_id)
    attempts = Attempts(run_id)
    notes = NotesManager(run_id, config.notes)
    context = TaskContexts(run_id).get(task_id) or TaskContext(task_id=task_id)
    verification_result = _load_verification_result(verification_result_ref)
    resolved_artifact_ref = str(artifact_ref or verification_result.get("artifact_ref") or "")
    if not resolved_artifact_ref:
        raise ValueError(
            "artifact_ref must be supplied or present in verification_result_ref JSON."
        )

    # A verifier path like .../attempt_001/eval_result.json names its attempt.
    parent_name = Path(verification_result_ref).parent.name
    inferred_attempt_id = parent_name if parent_name.startswith("attempt_") else None
    resolved_attempt_id = str(
        attempt_id
        or verification_result.get("attempt_id")
        or inferred_attempt_id
        or task_id
        or uuid4()
    )
    existing_attempt = attempts.find_by_task_attempt(task_id, resolved_attempt_id)
    if existing_attempt is not None:
        existing_ref = existing_attempt.verification_result_ref
        if existing_ref and existing_ref != verification_result_ref:
            raise ValueError(
                "Attempt log already contains this task/attempt with a different verification_result_ref."
            )
        note_requests = (
            notes.public_requests_for_attempt(task_id, resolved_attempt_id)
            if config.notes.enabled
            else []
        )
        return _record_response_from_attempt(
            existing_attempt,
            note_requests=note_requests,
            relative_delta_to_parent=_relative_delta_to_parent(existing_attempt, pool),
        )

    subagent_id = str(verification_result.get("subagent_id") or "")
    parent_id = context.parent_id if context.parent_selected else None
    inspiration_ids = list(context.inspiration_ids)
    generation = pool.infer_next_generation(parent_id)

    status = str(verification_result.get("status", "")).lower()
    fitness = _extract_fitness(verification_result)
    verification_metrics = dict(verification_result.get("metrics") or {})
    best_before = _best_fitness(pool)
    in_success_pool = False
    success_record_id = None

    if status == "success" and fitness is not None:
        success_record_id = task_id
        if task_id in pool:
            # The pool keeps one record per task; a later success for the
            # same task is logged but does not enter the pool again.
            logger.warning(
                f"run {run_id}: task {task_id} succeeded again on attempt "
                f"{resolved_attempt_id} after already entering the success pool"
            )
        else:
            in_success_pool = True

    attempt = AttemptRecord(
        id=resolved_attempt_id,
        run_id=run_id,
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
    attempts.append(attempt)
    logger.info(
        f"run {run_id}: task {task_id} attempt {resolved_attempt_id} recorded, "
        f"status={status} fitness={fitness} in_success_pool={in_success_pool}"
    )

    final_task_attempt = status == "success" or _attempt_number(resolved_attempt_id) >= max_attempts
    if final_task_attempt:
        with TaskContexts(run_id) as contexts:
            saved = contexts.get(task_id, create=True)
            saved.recorded = True
            saved.verification_result_ref = verification_result_ref
            saved.in_success_pool = in_success_pool
            saved.success_record_id = success_record_id if in_success_pool else None

    pool = SuccessPool(run_id)
    best_after = _best_fitness(pool)
    if final_task_attempt:
        anchor_record = pool.get(context.anchor_parent_id) if context.anchor_parent_id else None
        MetaGuidanceManager(run_id, config.meta_evolve).record_attempt_outcome(
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
    if config.notes.enabled:
        note_requests = notes.build_note_requests(
            attempt=attempt,
            attempts=attempts,
            db=pool,
            max_attempts=max_attempts,
        )
    else:
        note_requests = []
    return _record_response_from_attempt(
        attempt,
        note_requests=note_requests,
        relative_delta_to_parent=_relative_delta_to_parent(attempt, pool),
    )


def commit_generated_note(run_id: str, note_request_id: str, generated_note_json: Any) -> Dict[str, Any]:
    """Commit one LLM-distilled run-local note."""
    config = _config(run_id)
    if not config.notes.enabled:
        return {
            "committed": False,
            "disabled": True,
            "reason": "Run-local Notes disabled in run config.",
        }
    return NotesManager(run_id, config.notes).commit_generated_note(note_request_id, generated_note_json)


def check_convergence(run_id: str, concurrency: int) -> Dict[str, Any]:
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
    config = _config(run_id)
    pool = SuccessPool(run_id)
    tree = _lineage(pool)
    meta_guidance = MetaGuidanceManager(run_id, config.meta_evolve)
    run_state = RunState(run_id)
    total_tasks = _task_count(run_id)
    total_attempts = len(Attempts(run_id))
    budget_reason = RunBudgetGuard(config.budget).exhaustion_reason(
        total_tasks=total_tasks,
        elapsed_seconds=run_state.elapsed_seconds(),
    )
    budget_exhausted = bool(budget_reason)
    state, reason, diagnostics = FitnessPlateauDetector(config.plateau).evaluate(
        run_state,
        pool,
        tree,
        stop_reason=budget_reason,
    )
    action = "continue"
    request_id = None

    if budget_exhausted:
        action = "budget_exhausted"
    elif state == SearchState.WATCHING:
        current_best = diagnostics.get("current_best_fitness")
        meta_guidance.note_watching(
            task_count=total_tasks,
            current_best_fitness=current_best,
        )
        if meta_guidance.should_request_guidance(current_best_fitness=current_best):
            lineages = [
                {
                    "root_id": root_id,
                    "size": stats.size,
                    "best_fitness": stats.best_fitness,
                    "best_record_id": stats.best_record_id,
                    "max_depth": stats.max_depth,
                    "share": stats.share,
                    "recent_improvement": stats.recent_improvement,
                }
                for root_id, stats in sorted(tree.get_all_lineage_stats().items())
            ]
            request = meta_guidance.ensure_pending_request(
                db=pool,
                task_description=run_state.task_description,
                diagnostics=diagnostics,
                lineages=lineages,
                notes_summary=(
                    NotesManager(run_id, config.notes).summarize_for_meta_guidance()
                    if config.notes.enabled
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
                request_id = request.request_id
    else:
        meta_guidance.reset_watching()

    tasks_to_create = 0
    if action == "continue":
        root_number = config.scheduler.lineage_root_number
        max_total = config.budget.max_total_tasks
        # During the root phase minting stays capped at root_number: that
        # both limits the phase to one task per direction and makes
        # top_up_tasks return [] during the root barrier (all roots
        # minted, some still in flight).
        mint_cap = min(root_number, max_total) if _in_root_phase(run_id, config) else max_total
        newly_created = scheduler.top_up_tasks(run_id, concurrency, mint_cap)
        tasks_to_create = len(newly_created)

    meta_stats = meta_guidance.stats()
    diagnostics["meta_guidance"] = meta_stats
    if state != run_state.search_state:
        logger.info(
            f"run {run_id}: search state {run_state.search_state.value} -> {state.value}"
            + (f" ({reason})" if reason else "")
        )
    logger.info(f"run {run_id}: check_convergence action={action} tasks_to_create={tasks_to_create}")
    with RunState(run_id) as saved:
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


def get_meta_guidance_request(run_id: str, request_id: str) -> Dict[str, Any]:
    """Return a pending meta-guidance request."""
    config = _config(run_id)
    return MetaGuidanceManager(run_id, config.meta_evolve).get_request(request_id or None)


def commit_meta_guidance(run_id: str, request_id: str, guidance_json: Any) -> Dict[str, Any]:
    """Validate and activate worker-generated meta-guidance."""
    config = _config(run_id)
    return MetaGuidanceManager(run_id, config.meta_evolve).commit_guidance(request_id, guidance_json)


def get_meta_guidance_prompt(run_id: str, task_id: str) -> Dict[str, Any]:
    """Return the meta-guidance prompt assigned during parent selection."""
    config = _config(run_id)
    context = TaskContexts(run_id).get(task_id)
    if context is None or not context.parent_selected:
        raise ValueError("Call select_parent before get_meta_guidance_prompt.")
    return MetaGuidanceManager(run_id, config.meta_evolve).prompt_for_task(task_id)


def finalize_run(run_id: str, stop_reason: Optional[str]) -> Dict[str, str]:
    """Write deterministic run summary artifacts and return compact refs.

    Finalization is intentionally idempotent.  Repeated calls regenerate the
    same report files from current persisted state without changing
    SuccessPool or attempt history.
    """
    with RunState(run_id) as run_state:
        run_state.search_state = SearchState.STOPPED
        run_state.stop_reason = stop_reason or run_state.stop_reason or "manual_finalize"
        run_state.last_checked_attempt_count = len(Attempts(run_id))
    pool = SuccessPool(run_id)
    return write_run_report(
        run_id=run_id,
        db=pool,
        lineage_tree=_lineage(pool),
        run_state=RunState(run_id),
    )


def _config(run_id: str) -> Config:
    config = Config(run_id)
    if not config.created_at:
        logger.warning(f"run {run_id}: no config.json yet, using defaults")
    return config


def _lineage(pool: SuccessPool) -> LineageTree:
    tree = LineageTree()
    tree.rebuild(pool)
    return tree


def _in_root_phase(run_id: str, config: Config) -> bool:
    """True until all ``lineage_root_number`` roots have finished.

    Completed and failed pool tasks both count as finished. The count is
    monotonic and evolution tasks are only minted after it reaches the
    root number, so this flips exactly once per run.
    """
    pool = scheduler.pool_snapshot(run_id)
    finished = len(pool.completed) + len(pool.failed)
    return finished < config.scheduler.lineage_root_number


def _progress(run_id: str, config: Config) -> float:
    """Compute search progress internally from registered tasks."""
    max_total = max(config.budget.max_total_tasks, 1)
    return min(_task_count(run_id) / max_total, 1.0)


def _task_count(run_id: str) -> int:
    """Return the number of unique tasks registered in this run.

    Attempts intentionally retains every retry for Notes and diagnostics,
    so its row count must not be used as the task-budget counter.
    """
    attempt_task_ids = {a.task_id for a in Attempts(run_id) if a.task_id}
    return max(len(TaskContexts(run_id)), len(attempt_task_ids))


def _parent_ref(pool: SuccessPool, parent_id: Optional[str]) -> Dict[str, Any]:
    """Return the compact public parent payload, including diagnostics."""
    if parent_id is None:
        return {
            "parent_id": None,
            "artifact_ref": None,
            "fitness": None,
            "metrics": {},
        }
    record = pool.get(parent_id)
    return {
        "parent_id": parent_id,
        "artifact_ref": record.artifact_ref if record else None,
        "fitness": record.fitness if record else None,
        "metrics": dict(record.metrics or {}) if record else {},
    }


def _inspiration_refs(pool: SuccessPool, inspiration_ids: List[str]) -> Dict[str, Any]:
    """Return compact public inspiration payloads in cached order."""
    inspirations = []
    for inspiration_id in inspiration_ids:
        record = pool.get(inspiration_id)
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


def _best_fitness(pool: SuccessPool) -> Optional[float]:
    best = pool.get_canonical_best_record()
    return best.fitness if best else None


def _attempt_number(attempt_id: str) -> int:
    text = str(attempt_id)
    if "_" in text:
        text = text.rsplit("_", 1)[-1]
    try:
        return int(text)
    except ValueError:
        return 1


def _relative_delta_to_parent(attempt: AttemptRecord, pool: SuccessPool) -> Optional[float]:
    parent = pool.get(str(attempt.parent_id)) if attempt.parent_id else None
    if parent is None:
        return None
    fitness = _extract_fitness(attempt.verification_result)
    if fitness is None:
        return None
    denom = max(abs(parent.fitness), 1e-6)
    return (fitness - parent.fitness) / denom


def _record_response_from_attempt(
    attempt: AttemptRecord,
    *,
    note_requests: List[Dict[str, Any]],
    relative_delta_to_parent: Optional[float],
) -> Dict[str, Any]:
    """Return the public attempt-recording payload for Notes."""
    return {
        "recorded": True,
        "attempt_id": attempt.id,
        "in_success_pool": attempt.in_success_pool,
        "success_record_id": attempt.success_record_id if attempt.in_success_pool else None,
        "fitness": _extract_fitness(attempt.verification_result),
        "relative_delta_to_parent": relative_delta_to_parent,
        "note_requests": note_requests,
    }

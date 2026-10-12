"""Meta-guidance lifecycle for the Evolution Controller MCP.

Meta-guidance is a lightweight meta-evolve loop: when the search plateaus,
the controller asks a dedicated agent for a few distinct strategy ideas,
hands each idea to a task, and records how those tasks fared so the next
request can avoid repeating what did not work.

State lives in ``MetaGuidanceState`` (source of truth); every lifecycle step
is also appended to ``MetaGuidanceEvents`` so one file tells the whole story.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from evotrias.db.config import MetaEvolveConfig
from evotrias.db.meta_guidance_events import MetaGuidanceEvent, MetaGuidanceEvents
from evotrias.db.meta_guidance_state import (
    Assignment,
    Guidance,
    MetaGuidanceState,
    PendingRequest,
    RequestProgress,
    Watching,
)
from evotrias.evolve_ctrl.success_pool import SuccessPool, SuccessRecord
from evotrias.utils.time import utc_now_iso


class MetaGuidanceManager:
    """Manage request, active guidance, use counts, and simple outcomes."""

    def __init__(self, run_id: str, config: MetaEvolveConfig):
        self.run_id = run_id
        self.config = config
        self.events = MetaGuidanceEvents(run_id)

    def reset_watching(self) -> None:
        """Clear WATCHING marker and any uncommitted request when plateau ends."""
        with MetaGuidanceState(self.run_id) as state:
            watching = state.watching
            had_pending = state.pending_request is not None
            state.watching = None
            state.pending_request = None
        if watching is not None:
            self._append_event(
                "watching_reset",
                {
                    "started_task_count": watching.started_task_count,
                    "best_fitness": watching.best_fitness,
                    "had_pending_request": had_pending,
                },
            )

    def note_watching(self, *, task_count: int, current_best_fitness: Optional[float]) -> None:
        """Record the first WATCHING point for the current plateau."""
        if MetaGuidanceState(self.run_id).watching is not None:
            return
        with MetaGuidanceState(self.run_id) as state:
            state.watching = Watching(
                started_task_count=int(task_count),
                best_fitness=current_best_fitness,
            )
        self._append_event(
            "watching_started",
            {"started_task_count": int(task_count), "best_fitness": current_best_fitness},
        )

    def should_request_guidance(
        self,
        *,
        current_best_fitness: Optional[float],
    ) -> bool:
        """Return whether the current plateau can request guidance."""
        if not self.config.enabled:
            return False
        state = MetaGuidanceState(self.run_id)
        if state.active_guidance:
            return False
        if state.pending_request is not None:
            return True
        watching = state.watching
        if watching is None or current_best_fitness is None:
            return False
        if watching.best_fitness is not None and current_best_fitness > float(watching.best_fitness):
            return False
        return True

    def ensure_pending_request(
        self,
        *,
        db: SuccessPool,
        task_description: str,
        diagnostics: Dict[str, Any],
        lineages: List[Dict[str, Any]],
        notes_summary: Optional[Dict[str, Any]] = None,
        task_count: int,
    ) -> Optional[PendingRequest]:
        """Create or return the current pending request."""
        existing = MetaGuidanceState(self.run_id).pending_request
        if existing is not None:
            return existing

        anchor = db.get_canonical_best_record()
        if anchor is None:
            return None

        with MetaGuidanceState(self.run_id) as state:
            request_number = state.next_request_index
            state.next_request_index = request_number + 1
            completed_since_watching = _completed_since_watching(state, task_count)
            request = PendingRequest(
                request_id=state.next_id("mgr", request_number),
                created_at_task_count=int(task_count),
                task_description=task_description,
                current_best=_record_ref(anchor),
                progress=RequestProgress(
                    completed_since_watching=completed_since_watching,
                    best_at_watching_start=state.watching.best_fitness if state.watching else None,
                    current_best_fitness=anchor.fitness,
                    diagnostics=dict(diagnostics or {}),
                ),
                lineages=lineages,
                notes_summary=notes_summary
                or {"enabled": False, "positive": [], "negative": [], "error": [], "errors": []},
                guidance_history=_guidance_history(state),
                failed_guidance=_failed_guidance(state),
                generation_instructions=_generation_instructions(),
                constraints=[
                    "Do not bypass the evaluator.",
                    "Keep the benchmark output contract.",
                    "Do not modify verifier/evaluator source or use it to hard-code evaluator-specific shortcuts.",
                    "You may inspect current_best.artifact_ref or the provided artifact_excerpt; this is candidate code.",
                ],
            )
            state.pending_request = request
        self._append_event(
            "request_created",
            {
                "request_id": request.request_id,
                "created_at_task_count": request.created_at_task_count,
                "current_best_fitness": anchor.fitness,
                "completed_since_watching": completed_since_watching,
            },
        )
        return request

    def get_request(self, request_id: Optional[str] = None) -> Dict[str, Any]:
        """Return the pending request payload for the meta-guidance worker."""
        pending = MetaGuidanceState(self.run_id).pending_request
        if pending is None:
            return {"available": False, "reason": "no_pending_meta_guidance_request"}
        if request_id and pending.request_id != request_id:
            return {"available": False, "reason": "meta_guidance_request_not_found"}
        payload = asdict(pending)
        current_best = payload["current_best"]
        if current_best:
            # Read the candidate code now rather than at request creation so the
            # state file carries only the artifact_ref, not a 16 KB snapshot.
            excerpt, truncated = _read_artifact_excerpt(current_best.get("artifact_ref", ""))
            current_best["artifact_excerpt"] = excerpt
            current_best["artifact_excerpt_truncated"] = truncated
        return {"available": True, **payload}

    def commit_guidance(self, request_id: str, guidance_json: Any) -> Dict[str, Any]:
        """Validate and activate guidance generated by the meta-guidance worker."""
        if not self.config.enabled:
            return {"committed": False, "reason": "meta_guidance_disabled"}
        pending = MetaGuidanceState(self.run_id).pending_request
        if pending is None or pending.request_id != request_id:
            return {"committed": False, "reason": "pending_request_not_found"}

        raw_guidance = _extract_guidance_list(guidance_json)
        if not raw_guidance:
            return {"committed": False, "reason": "empty_guidance"}
        if len(raw_guidance) > self.config.guidance_num_to_generate:
            return {
                "committed": False,
                "reason": "too_many_guidance_items",
                "max": self.config.guidance_num_to_generate,
            }

        anchor_id = str(pending.current_best.get("record_id") or "")
        if not anchor_id:
            return {"committed": False, "reason": "missing_anchor_parent"}

        # Validate everything before touching state so a rejected batch leaves
        # the pending request intact for a retry.
        validated_items: List[Dict[str, Any]] = []
        seen_approach_types = set()
        for item in raw_guidance:
            validated = _validate_guidance_item(item)
            if isinstance(validated, str):
                return {"committed": False, "reason": validated}
            approach_key = str(validated.get("approach_type") or "").strip().lower()
            if approach_key in seen_approach_types:
                return {"committed": False, "reason": "duplicate_approach_type"}
            seen_approach_types.add(approach_key)
            try:
                max_uses = int(validated.get("max_uses") or self.config.guidance_max_uses)
            except (TypeError, ValueError):
                return {"committed": False, "reason": "invalid_max_uses"}
            validated["max_uses"] = max(1, min(max_uses, self.config.guidance_max_uses))
            validated_items.append(validated)

        with MetaGuidanceState(self.run_id) as state:
            start_index = state.next_guidance_index
            created: List[Guidance] = []
            for offset, validated in enumerate(validated_items):
                guidance = Guidance(
                    **validated,
                    guidance_id=state.next_id("mg", start_index + offset),
                    request_id=request_id,
                    anchor_parent_id=anchor_id,
                    creation_anchor_parent_id=anchor_id,
                    created_at_task_count=pending.created_at_task_count,
                    best_fitness_at_creation=pending.current_best.get("fitness"),
                )
                state.active_guidance.append(guidance)
                created.append(guidance)
            state.next_guidance_index = start_index + len(created)
            state.pending_request = None

        for guidance in created:
            self._append_event(
                "guidance_committed",
                {
                    "request_id": request_id,
                    "guidance_id": guidance.guidance_id,
                    "idea": guidance.idea,
                    "approach_type": guidance.approach_type,
                    "anchor_parent_id": anchor_id,
                    "max_uses": guidance.max_uses,
                },
            )
        return {
            "committed": True,
            "request_id": request_id,
            "guidance_ids": [item.guidance_id for item in created],
            "anchor_parent_id": anchor_id,
        }

    def assign_for_task(
        self,
        task_id: str,
        anchor_parent_id: Optional[str] = None,
        anchor_fitness: Optional[float] = None,
    ) -> Optional[Assignment]:
        """Assign one active guidance item to a task, consuming one use."""
        state = MetaGuidanceState(self.run_id)
        existing = state.task_assignments.get(task_id)
        if existing is not None:
            guidance = state.active_by_id(existing.guidance_id) or _tried_by_id(state, existing.guidance_id)
            if guidance is None:
                return None
            return existing

        if not self.config.enabled:
            return None

        with MetaGuidanceState(self.run_id) as state:
            for guidance in state.active_guidance:
                if guidance.uses >= guidance.max_uses:
                    continue
                guidance.uses += 1
                assignment = Assignment(
                    task_id=task_id,
                    guidance_id=guidance.guidance_id,
                    anchor_parent_id=str(anchor_parent_id or guidance.anchor_parent_id),
                    anchor_fitness=_coerce_float(anchor_fitness),
                    creation_anchor_parent_id=guidance.creation_anchor_parent_id or guidance.anchor_parent_id,
                )
                state.task_assignments[task_id] = assignment
                break
            else:
                return None
        self._append_event("assigned", asdict(assignment))
        return assignment

    def prompt_for_task(self, task_id: str) -> Dict[str, Any]:
        """Return a ready-to-inject prompt for a task's assigned guidance."""
        state = MetaGuidanceState(self.run_id)
        assignment = state.task_assignments.get(task_id)
        if assignment is None:
            return {"available": False, "reason": "no_active_guidance"}
        guidance = state.active_by_id(assignment.guidance_id) or _tried_by_id(state, assignment.guidance_id)
        if guidance is None:
            return {"available": False, "reason": "assigned_guidance_not_found"}
        return {
            "available": True,
            "guidance_id": guidance.guidance_id,
            "anchor_parent_id": assignment.anchor_parent_id or guidance.anchor_parent_id,
            "anchor_fitness": assignment.anchor_fitness,
            "creation_anchor_parent_id": guidance.creation_anchor_parent_id or guidance.anchor_parent_id,
            "prompt": _render_guidance_prompt(guidance),
        }

    def record_attempt_outcome(
        self,
        *,
        task_id: str,
        attempt_id: str,
        valid_result: bool,
        in_success_pool: bool,
        success_record_id: Optional[str],
        fitness: Optional[float],
        anchor_fitness: Optional[float],
        best_fitness_before: Optional[float],
        best_fitness_after: Optional[float],
        metrics: Optional[Dict[str, Any]] = None,
        anchor_metrics: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record the outcome of an attempt that used guidance."""
        with MetaGuidanceState(self.run_id) as state:
            assignment = state.task_assignments.get(task_id)
            if assignment is None:
                return
            guidance = state.active_by_id(assignment.guidance_id)
            if guidance is None:
                return

            valid_result = bool(valid_result and fitness is not None)
            actual_anchor_fitness = assignment.anchor_fitness
            if actual_anchor_fitness is None:
                actual_anchor_fitness = _coerce_float(anchor_fitness)
            delta_vs_anchor = (
                fitness - actual_anchor_fitness
                if fitness is not None and valid_result and actual_anchor_fitness is not None
                else None
            )
            relative_delta_vs_anchor = (
                delta_vs_anchor / max(abs(actual_anchor_fitness), 1e-9)
                if delta_vs_anchor is not None and actual_anchor_fitness is not None
                else None
            )
            improved_anchor = bool(delta_vs_anchor is not None and delta_vs_anchor > 0)
            metrics_changed = not _values_equivalent(metrics or {}, anchor_metrics or {})
            no_effect = bool(
                valid_result
                and delta_vs_anchor is not None
                and _numbers_equivalent(delta_vs_anchor, 0.0)
                and not metrics_changed
            )
            created_new_best = (
                valid_result and fitness is not None and (best_fitness_before is None or fitness > best_fitness_before)
            )
            if valid_result:
                guidance.has_valid_result = True
            if improved_anchor:
                guidance.improved_anchor = True
            if created_new_best:
                guidance.created_new_best = True
            guidance.outcomes.append(
                {
                    "task_id": task_id,
                    "attempt_id": attempt_id,
                    "valid_result": valid_result,
                    "in_success_pool": in_success_pool,
                    "success_record_id": success_record_id,
                    "fitness": fitness,
                    "anchor_parent_id": assignment.anchor_parent_id,
                    "anchor_fitness": actual_anchor_fitness,
                    "delta_vs_anchor": delta_vs_anchor,
                    "relative_delta_vs_anchor": relative_delta_vs_anchor,
                    "improved_anchor": improved_anchor,
                    "metrics_changed": metrics_changed,
                    "no_effect": no_effect,
                    "best_fitness_before": best_fitness_before,
                    "best_fitness_after": best_fitness_after,
                    "created_new_best": created_new_best,
                }
            )

            assignment.status = "recorded"
            assignment.attempt_id = attempt_id
            assignment.valid_result = valid_result
            assignment.in_success_pool = in_success_pool
            assignment.success_record_id = success_record_id
            assignment.fitness = fitness
            assignment.anchor_fitness = actual_anchor_fitness
            assignment.delta_vs_anchor = delta_vs_anchor
            assignment.relative_delta_vs_anchor = relative_delta_vs_anchor
            assignment.improved_anchor = improved_anchor
            assignment.metrics_changed = metrics_changed
            assignment.no_effect = no_effect
            assignment.best_fitness_before = best_fitness_before
            assignment.best_fitness_after = best_fitness_after
            assignment.created_new_best = created_new_best
            assignment.recorded_at = utc_now_iso()

            archived = _archive_exhausted_if_ready(state, guidance.guidance_id)

        self._append_event("recorded", asdict(assignment))
        if archived is not None:
            self._append_event(
                "guidance_archived",
                {
                    "guidance_id": archived.guidance_id,
                    "outcome": archived.outcome,
                    "uses": archived.uses,
                    "outcome_summary": archived.outcome_summary,
                },
            )

    def stats(self) -> Dict[str, Any]:
        """Return compact meta-guidance diagnostics."""
        state = MetaGuidanceState(self.run_id)
        return {
            "enabled": self.config.enabled,
            "has_pending_request": state.pending_request is not None,
            "active_count": len(state.active_guidance),
            "active_with_remaining": sum(1 for g in state.active_guidance if g.uses < g.max_uses),
            "tried_count": len(state.tried_guidance),
            "watching": asdict(state.watching) if state.watching else None,
        }

    def _append_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        self.events.append(MetaGuidanceEvent(event_type=event_type, run_id=self.run_id, payload=payload))


def _archive_exhausted_if_ready(state: MetaGuidanceState, guidance_id: str) -> Optional[Guidance]:
    """Move a fully used guidance from active to tried; return it if archived."""
    guidance = state.active_by_id(guidance_id)
    if guidance is None or guidance.uses < guidance.max_uses:
        return None
    if any(a.guidance_id == guidance_id and a.status == "assigned" for a in state.task_assignments.values()):
        return None

    guidance.status = "exhausted"
    guidance.outcome = _classify_guidance_outcome(guidance)
    guidance.outcome_summary = _guidance_outcome_summary(guidance)
    state.active_guidance = [g for g in state.active_guidance if g.guidance_id != guidance_id]
    state.tried_guidance.append(guidance)
    return guidance


def _tried_by_id(state: MetaGuidanceState, guidance_id: str) -> Optional[Guidance]:
    return next((g for g in state.tried_guidance if g.guidance_id == guidance_id), None)


def _failed_guidance(state: MetaGuidanceState) -> List[Dict[str, Any]]:
    return [
        {
            "guidance_id": item.guidance_id,
            "idea": item.idea,
            "approach_type": item.approach_type,
            "uses": item.uses,
            "outcome": item.outcome,
            "outcome_summary": item.outcome_summary or _guidance_outcome_summary(item),
        }
        for item in state.tried_guidance
        if item.outcome in {"no_effect", "regression", "failed"}
    ]


def _guidance_history(state: MetaGuidanceState) -> List[Dict[str, Any]]:
    """Return prior guidance with deterministic outcome summaries."""
    return [
        {
            "guidance_id": item.guidance_id,
            "idea": item.idea,
            "approach_type": item.approach_type,
            "relationship_to_current_best": item.relationship_to_current_best,
            "uses": item.uses,
            "max_uses": item.max_uses,
            "outcome": item.outcome,
            "best_fitness_at_creation": item.best_fitness_at_creation,
            "outcome_summary": item.outcome_summary or _guidance_outcome_summary(item),
        }
        for item in state.tried_guidance
    ]


def _completed_since_watching(state: MetaGuidanceState, task_count: int) -> int:
    if state.watching is None:
        return 0
    return max(int(task_count) - state.watching.started_task_count, 0)


def _record_ref(record: SuccessRecord) -> Dict[str, Any]:
    return {
        "record_id": record.id,
        "fitness": record.fitness,
        "artifact_ref": record.artifact_ref,
        "summary": record.sketch,
        "metrics": dict(record.metrics or {}),
        "profile": dict(record.profile or {}),
        "created_at": record.created_at,
    }


def _extract_guidance_list(guidance_json: Any) -> List[Dict[str, Any]]:
    if isinstance(guidance_json, str):
        try:
            guidance_json = json.loads(guidance_json)
        except json.JSONDecodeError:
            return []
    if isinstance(guidance_json, dict):
        value = guidance_json.get("guidance")
    else:
        value = guidance_json
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _validate_guidance_item(item: Dict[str, Any]) -> Dict[str, Any] | str:
    """Check one agent-submitted guidance item; return its normalised fields or a reason."""
    required = ["idea", "description", "novelty_claim", "implementation_plan", "approach_type"]
    for key in required:
        value = item.get(key)
        if value is None or value == "" or value == []:
            return f"missing_required_field:{key}"
    text = " ".join(str(item.get(key, "")) for key in ["idea", "description", "novelty_claim", "approach_type"]).lower()
    forbidden = ["read verifier", "read evaluator", "bypass evaluator", "modify verifier"]
    if any(term in text for term in forbidden):
        return "unsafe_guidance"
    return {
        "idea": str(item["idea"]),
        "description": str(item["description"]),
        "target_metric": str(item.get("target_metric") or "fitness"),
        "novelty_claim": str(item["novelty_claim"]),
        "current_best_assessment": str(item.get("current_best_assessment") or ""),
        "relationship_to_current_best": str(item.get("relationship_to_current_best") or "critically_adapt"),
        "implementation_plan": [str(value) for value in item.get("implementation_plan") or []],
        "cautions": [str(value) for value in item.get("cautions") or []],
        "approach_type": str(item["approach_type"]),
        "avoid_repeating": [str(value) for value in item.get("avoid_repeating") or []],
        "max_uses": item.get("max_uses"),
    }


def _render_guidance_prompt(guidance: Guidance) -> str:
    plan = "\n".join(f"- {item}" for item in guidance.implementation_plan)
    cautions = "\n".join(f"- {item}" for item in guidance.cautions)
    avoid = "\n".join(f"- {item}" for item in guidance.avoid_repeating)
    return (
        "## META-GUIDANCE - APPLY THIS\n\n"
        "The search has stagnated and meta-guidance was requested. "
        "You MUST make a purposeful attempt to apply this guidance.\n\n"
        f"**IDEA:** {guidance.idea}\n\n"
        f"**WHY THIS IS NEW:** {guidance.novelty_claim}\n\n"
        f"**CURRENT BEST ASSESSMENT:** "
        f"{guidance.current_best_assessment or 'Use the current best critically; do not assume its strategy is optimal.'}\n\n"
        f"**RELATIONSHIP TO CURRENT BEST:** {guidance.relationship_to_current_best}\n\n"
        f"**HOW TO IMPLEMENT:**\n{guidance.description}\n\n"
        f"**IMPLEMENTATION PLAN:**\n{plan or '- Apply the idea directly.'}\n\n"
        f"**TARGET METRIC:** {guidance.target_metric}\n\n"
        f"**APPROACH TYPE:** {guidance.approach_type}\n\n"
        f"**CAUTIONS:**\n{cautions or '- Keep the benchmark output contract.'}\n\n"
        f"**AVOID REPEATING:**\n{avoid or '- No specific failed route listed.'}\n\n"
        "**ANCHOR RULES:**\n"
        "This guidance is anchored on the selected parent.\n"
        "Use the selected parent as an interface and current-state reference, not as proof that its strategy is correct.\n"
        "Do not silently return the parent or another algorithm when the required strategy fails; the evaluated result must remain attributable to the claimed strategy.\n"
        "If the guidance says to depart from or replace the current approach, implement the new strategy directly.\n"
        "Do not force a superficial combination with inspirations if they conflict with the guidance.\n"
    )


def _read_artifact_excerpt(artifact_ref: str, max_chars: int = 16000) -> tuple[str, bool]:
    if not artifact_ref:
        return "", False
    try:
        path = Path(artifact_ref).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.is_file():
            return "", False
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) <= max_chars:
            return text, False
        return text[:max_chars], True
    except OSError:
        return "", False


def _guidance_outcome_summary(guidance: Guidance) -> Dict[str, Any]:
    outcomes = guidance.outcomes
    fitness_values = [value for value in (_coerce_float(item.get("fitness")) for item in outcomes) if value is not None]
    anchor_deltas = [
        value for value in (_coerce_float(item.get("delta_vs_anchor")) for item in outcomes) if value is not None
    ]
    valid_uses = sum(1 for item in outcomes if _is_valid_guidance_outcome(item))
    return {
        "total_uses": len(outcomes) or guidance.uses,
        "valid_uses": valid_uses,
        "failed_uses": len(outcomes) - valid_uses,
        "no_effect_uses": sum(1 for item in outcomes if _is_no_effect_guidance_outcome(item)),
        "improved_anchor_uses": sum(
            1
            for item in outcomes
            if item.get("improved_anchor") or (_coerce_float(item.get("delta_vs_anchor")) or 0.0) > 0
        ),
        "new_best_uses": sum(1 for item in outcomes if item.get("created_new_best")),
        "best_fitness": max(fitness_values) if fitness_values else None,
        "best_delta_vs_anchor": max(anchor_deltas) if anchor_deltas else None,
    }


def _classify_guidance_outcome(guidance: Guidance) -> str:
    outcomes = guidance.outcomes
    if any(item.get("created_new_best") for item in outcomes):
        return "new_best"
    if any(item.get("improved_anchor") or (_coerce_float(item.get("delta_vs_anchor")) or 0.0) > 0 for item in outcomes):
        return "improved_anchor"
    valid_outcomes = [item for item in outcomes if _is_valid_guidance_outcome(item)]
    if valid_outcomes and all(_is_no_effect_guidance_outcome(item) for item in valid_outcomes):
        return "no_effect"
    if valid_outcomes:
        return "regression"
    return "failed"


def _is_valid_guidance_outcome(outcome: Dict[str, Any]) -> bool:
    if "valid_result" in outcome:
        return bool(outcome.get("valid_result"))
    return bool(outcome.get("in_success_pool"))


def _is_no_effect_guidance_outcome(outcome: Dict[str, Any]) -> bool:
    return _is_valid_guidance_outcome(outcome) and bool(outcome.get("no_effect"))


def _values_equivalent(left: Any, right: Any) -> bool:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(_values_equivalent(left[key], right[key]) for key in left)
    if (
        isinstance(left, Sequence)
        and not isinstance(left, (str, bytes, bytearray))
        and isinstance(right, Sequence)
        and not isinstance(right, (str, bytes, bytearray))
    ):
        return len(left) == len(right) and all(
            _values_equivalent(left_value, right_value) for left_value, right_value in zip(left, right)
        )
    if (
        isinstance(left, (int, float))
        and not isinstance(left, bool)
        and isinstance(right, (int, float))
        and not isinstance(right, bool)
    ):
        return _numbers_equivalent(float(left), float(right))
    return left == right


def _numbers_equivalent(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-15)


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _generation_instructions() -> List[str]:
    return [
        "Inspect current_best.artifact_excerpt when present; if it is empty, use current_best.artifact_ref as the candidate implementation path.",
        "Inspect current_best.metrics and profile alongside fitness. Use metric components and scenario breakdowns as diagnostic evidence about where candidates differ or stagnate, without treating them as independent objectives unless the benchmark defines them that way.",
        "Analyze the current best critically: identify what works, what likely traps the search, and whether the next guidance should refine, depart from, or replace that route.",
        "Before crediting an internal algorithm, inspect the artifact control flow and available verification evidence to establish which primary, fallback, dispatch, or selector branch produced the evaluated result. Verifier success alone does not establish branch execution; if branch identity is unknown, treat the claimed strategy as unverified.",
        "Use notes_summary and guidance_history as evidence. Do not repeat guidance whose prior outcome_summary shows no improvement unless the failure condition is materially changed.",
        "Interpret prior outcomes precisely: new_best beat the global best at completion, improved_anchor beat its assigned parent but not the then-current global best, no_effect produced a valid result indistinguishable from its assigned parent in fitness and evaluator metrics, regression produced a different valid result without beating its assigned parent, and failed produced no valid result. no_effect is observational and does not by itself prove that fallback occurred.",
        "Generate distinct guidance items with different approach_type values. Avoid minor parameter variants of the same algorithm family.",
        "Prefer concrete, implementable strategies. Each item should say how it relates to the current best and why it can escape or improve the observed plateau.",
    ]

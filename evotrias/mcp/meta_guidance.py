"""Lightweight meta-guidance lifecycle management.

The MCP owns deterministic state and attribution.  It does not call an LLM;
a dedicated, stateless meta-guidance worker generates structured guidance only
when a pending request is returned by the controller.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Dict, List, Optional

from evotrias.db.models import MetaEvolveConfig, SuccessRecord, utc_now_iso
from evotrias.mcp.storage import (
    append_jsonl,
    meta_guidance_state_path,
    meta_guidance_uses_path,
    read_json,
    write_json_atomic,
)
from evotrias.mcp.success_db import SuccessDB


class MetaGuidanceManager:
    """Manage request, active guidance, use counts, and simple outcomes."""

    def __init__(self, run_id: str, config: MetaEvolveConfig):
        self.run_id = run_id
        self.config = config
        self.state_file = meta_guidance_state_path(run_id)
        self.uses_file = meta_guidance_uses_path(run_id)
        self.state: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        """Refresh state from disk."""
        self.state = read_json(self.state_file, self._empty_state())

    def save(self) -> None:
        """Persist state."""
        write_json_atomic(self.state_file, self.state)

    def reset_watching(self) -> None:
        """Clear WATCHING marker and any uncommitted request when plateau ends."""
        self.load()
        self.state["watching"] = None
        self.state["pending_request"] = None
        self.save()

    def note_watching(self, *, task_count: int, current_best_fitness: Optional[float]) -> None:
        """Record the first WATCHING point for the current plateau."""
        self.load()
        if self.state.get("watching") is None:
            self.state["watching"] = {
                "started_task_count": int(task_count),
                "best_fitness": current_best_fitness,
                "created_at": utc_now_iso(),
            }
            self.save()

    def should_request_guidance(
        self,
        *,
        current_best_fitness: Optional[float],
    ) -> bool:
        """Return whether the current plateau can request guidance."""
        self.load()
        if not self.config.enabled:
            return False
        if self.state.get("active_guidance"):
            return False
        if self.state.get("pending_request") is not None:
            return True
        watching = self.state.get("watching") or {}
        started = watching.get("started_task_count")
        if started is None or current_best_fitness is None:
            return False
        start_best = watching.get("best_fitness")
        if start_best is not None and current_best_fitness > float(start_best):
            return False
        return True

    def ensure_pending_request(
        self,
        *,
        db: SuccessDB,
        task_description: str,
        diagnostics: Dict[str, Any],
        lineages: List[Dict[str, Any]],
        notes_summary: Optional[Dict[str, Any]] = None,
        task_count: int,
    ) -> Optional[Dict[str, Any]]:
        """Create or return the current pending request."""
        self.load()
        pending = self.state.get("pending_request")
        if pending is not None:
            return dict(pending)

        anchor = db.get_canonical_best_record()
        if anchor is None:
            return None

        request_number = int(self.state["next_request_index"])
        self.state["next_request_index"] = request_number + 1
        request = {
            "request_id": self._next_id("mgr", request_number),
            "state": "pending",
            "created_at": utc_now_iso(),
            "created_at_task_count": int(task_count),
            "task_description": task_description,
            "current_best": _record_ref(anchor, include_artifact_excerpt=True),
            "progress": {
                "best_fitness_history": [],
                "completed_since_watching": self._completed_since_watching(task_count),
                "best_at_watching_start": (self.state.get("watching") or {}).get("best_fitness"),
                "current_best_fitness": anchor.fitness,
                "diagnostics": dict(diagnostics or {}),
            },
            "lineages": lineages,
            "notes_summary": notes_summary
            or {"enabled": False, "positive": [], "negative": [], "error": [], "errors": []},
            "guidance_history": self._guidance_history(),
            "failed_guidance": self._failed_guidance(),
            "generation_instructions": _generation_instructions(),
            "constraints": [
                "Do not bypass the evaluator.",
                "Keep the benchmark output contract.",
                "Do not modify verifier/evaluator source or use it to hard-code evaluator-specific shortcuts.",
                "You may inspect current_best.artifact_ref or the provided artifact_excerpt; this is candidate code.",
            ],
        }
        self.state["pending_request"] = request
        self.save()
        return dict(request)

    def get_request(self, request_id: Optional[str] = None) -> Dict[str, Any]:
        """Return the pending request payload for the meta-guidance worker."""
        self.load()
        pending = self.state.get("pending_request")
        if pending is None:
            return {"available": False, "reason": "no_pending_meta_guidance_request"}
        if request_id and pending.get("request_id") != request_id:
            return {"available": False, "reason": "meta_guidance_request_not_found"}
        return {"available": True, **dict(pending)}

    def commit_guidance(self, request_id: str, guidance_json: Any) -> Dict[str, Any]:
        """Validate and activate guidance generated by the meta-guidance worker."""
        self.load()
        if not self.config.enabled:
            return {"committed": False, "reason": "meta_guidance_disabled"}
        pending = self.state.get("pending_request")
        if pending is None or pending.get("request_id") != request_id:
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

        anchor_id = str((pending.get("current_best") or {}).get("record_id") or "")
        if not anchor_id:
            return {"committed": False, "reason": "missing_anchor_parent"}

        active = list(self.state.get("active_guidance") or [])
        created: List[Dict[str, Any]] = []
        seen_approach_types = set()
        start_index = int(self.state["next_guidance_index"])
        for offset, item in enumerate(raw_guidance):
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
            max_uses = max(1, min(max_uses, self.config.guidance_max_uses))
            guidance = {
                **validated,
                "guidance_id": self._next_id("mg", start_index + offset),
                "request_id": request_id,
                "anchor_parent_id": anchor_id,
                "creation_anchor_parent_id": anchor_id,
                "max_uses": max_uses,
                "uses": 0,
                "status": "active",
                "created_at": utc_now_iso(),
                "created_at_task_count": pending.get("created_at_task_count"),
                "best_fitness_at_creation": (pending.get("current_best") or {}).get("fitness"),
                "has_valid_result": False,
                "improved_anchor": False,
                "created_new_best": False,
                "outcomes": [],
            }
            active.append(guidance)
            created.append(guidance)

        self.state["active_guidance"] = active
        self.state["next_guidance_index"] = start_index + len(created)
        self.state["pending_request"] = None
        self.save()
        return {
            "committed": True,
            "request_id": request_id,
            "guidance_ids": [item["guidance_id"] for item in created],
            "anchor_parent_id": anchor_id,
        }

    def assign_for_task(
        self,
        task_id: str,
        anchor_parent_id: Optional[str] = None,
        anchor_fitness: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Assign one active guidance item to a task, consuming one use."""
        self.load()
        assignments = self.state.setdefault("task_assignments", {})
        existing_id = (assignments.get(task_id) or {}).get("guidance_id")
        if existing_id:
            guidance = self._active_by_id(existing_id) or self._tried_by_id(existing_id)
            if guidance is None:
                return None
            return self._with_assignment_anchor(guidance, assignments.get(task_id) or {})

        if not self.config.enabled:
            return None

        active = self.state.get("active_guidance") or []
        for guidance in active:
            if int(guidance.get("uses", 0)) >= int(guidance.get("max_uses", 1)):
                continue
            actual_anchor_id = str(anchor_parent_id or guidance["anchor_parent_id"])
            guidance["uses"] = int(guidance.get("uses", 0)) + 1
            assignment = {
                "task_id": task_id,
                "guidance_id": guidance["guidance_id"],
                "anchor_parent_id": actual_anchor_id,
                "anchor_fitness": _coerce_float(anchor_fitness),
                "creation_anchor_parent_id": guidance.get(
                    "creation_anchor_parent_id",
                    guidance["anchor_parent_id"],
                ),
                "status": "assigned",
                "assigned_at": utc_now_iso(),
            }
            assignments[task_id] = assignment
            self.save()
            append_jsonl(self.uses_file, assignment)
            return self._with_assignment_anchor(guidance, assignment)
        return None

    def prompt_for_task(self, task_id: str) -> Dict[str, Any]:
        """Return a ready-to-inject prompt for a task's assigned guidance."""
        self.load()
        assignment = (self.state.get("task_assignments") or {}).get(task_id) or {}
        guidance_id = assignment.get("guidance_id")
        if not guidance_id:
            return {"available": False, "reason": "no_active_guidance"}
        guidance = self._active_by_id(guidance_id) or self._tried_by_id(guidance_id)
        if guidance is None:
            return {"available": False, "reason": "assigned_guidance_not_found"}
        return {
            "available": True,
            "guidance_id": guidance["guidance_id"],
            "anchor_parent_id": assignment.get("anchor_parent_id") or guidance["anchor_parent_id"],
            "anchor_fitness": assignment.get("anchor_fitness"),
            "creation_anchor_parent_id": guidance.get(
                "creation_anchor_parent_id",
                guidance["anchor_parent_id"],
            ),
            "prompt": _render_guidance_prompt(guidance),
        }

    def record_attempt_outcome(
        self,
        *,
        task_id: str,
        attempt_id: str,
        valid_result: bool,
        entered_success_db: bool,
        success_record_id: Optional[str],
        fitness: Optional[float],
        anchor_fitness: Optional[float],
        best_fitness_before: Optional[float],
        best_fitness_after: Optional[float],
        metrics: Optional[Dict[str, Any]] = None,
        anchor_metrics: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record the outcome of an attempt that used guidance."""
        self.load()
        assignment = (self.state.get("task_assignments") or {}).get(task_id)
        if not assignment:
            return
        guidance = self._active_by_id(str(assignment.get("guidance_id")))
        if guidance is None:
            return

        valid_result = bool(valid_result and fitness is not None)
        actual_anchor_fitness = _coerce_float(assignment.get("anchor_fitness"))
        if actual_anchor_fitness is None:
            actual_anchor_fitness = _coerce_float(anchor_fitness)
        assignment["anchor_fitness"] = actual_anchor_fitness
        delta_vs_anchor = (
            fitness - actual_anchor_fitness
            if valid_result and actual_anchor_fitness is not None
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
            valid_result
            and fitness is not None
            and (best_fitness_before is None or fitness > best_fitness_before)
        )
        if valid_result:
            guidance["has_valid_result"] = True
        if improved_anchor:
            guidance["improved_anchor"] = True
        if created_new_best:
            guidance["created_new_best"] = True
        outcome = {
            "task_id": task_id,
            "attempt_id": attempt_id,
            "valid_result": valid_result,
            "entered_success_db": entered_success_db,
            "success_record_id": success_record_id,
            "fitness": fitness,
            "anchor_parent_id": assignment.get("anchor_parent_id"),
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
        outcomes = list(guidance.get("outcomes") or [])
        outcomes.append(outcome)
        guidance["outcomes"] = outcomes

        assignment.update(
            {
                "status": "recorded",
                "attempt_id": attempt_id,
                "valid_result": valid_result,
                "entered_success_db": entered_success_db,
                "success_record_id": success_record_id,
                "fitness": fitness,
                "anchor_fitness": actual_anchor_fitness,
                "delta_vs_anchor": delta_vs_anchor,
                "relative_delta_vs_anchor": relative_delta_vs_anchor,
                "improved_anchor": improved_anchor,
                "metrics_changed": metrics_changed,
                "no_effect": no_effect,
                "best_fitness_before": best_fitness_before,
                "best_fitness_after": best_fitness_after,
                "created_new_best": created_new_best,
                "recorded_at": utc_now_iso(),
            }
        )
        append_jsonl(self.uses_file, dict(assignment))
        self._archive_exhausted_if_ready(guidance["guidance_id"])
        self.save()

    def stats(self) -> Dict[str, Any]:
        """Return compact meta-guidance diagnostics."""
        self.load()
        active = list(self.state.get("active_guidance") or [])
        tried = list(self.state.get("tried_guidance") or [])
        return {
            "enabled": self.config.enabled,
            "has_pending_request": self.state.get("pending_request") is not None,
            "active_count": len(active),
            "active_with_remaining": len(self._active_with_remaining()),
            "tried_count": len(tried),
            "watching": self.state.get("watching"),
        }

    def _archive_exhausted_if_ready(self, guidance_id: str) -> None:
        assignments = self.state.get("task_assignments") or {}
        active = list(self.state.get("active_guidance") or [])
        guidance = next((item for item in active if item.get("guidance_id") == guidance_id), None)
        if guidance is None:
            return
        if int(guidance.get("uses", 0)) < int(guidance.get("max_uses", 1)):
            return
        assigned = [item for item in assignments.values() if item.get("guidance_id") == guidance_id]
        if any(item.get("status") == "assigned" for item in assigned):
            return

        outcome = _classify_guidance_outcome(guidance)
        archived = {
            **guidance,
            "status": "exhausted",
            "outcome": outcome,
            "outcome_summary": _guidance_outcome_summary(guidance),
        }
        self.state["active_guidance"] = [
            item for item in active if item.get("guidance_id") != guidance_id
        ]
        tried = list(self.state.get("tried_guidance") or [])
        tried.append(archived)
        self.state["tried_guidance"] = tried

    def _active_by_id(self, guidance_id: str) -> Optional[Dict[str, Any]]:
        for item in self.state.get("active_guidance") or []:
            if item.get("guidance_id") == guidance_id:
                return item
        return None

    def _tried_by_id(self, guidance_id: str) -> Optional[Dict[str, Any]]:
        for item in self.state.get("tried_guidance") or []:
            if item.get("guidance_id") == guidance_id:
                return item
        return None

    def _active_with_remaining(self) -> List[Dict[str, Any]]:
        return [
            item
            for item in self.state.get("active_guidance") or []
            if int(item.get("uses", 0)) < int(item.get("max_uses", 1))
        ]

    @staticmethod
    def _with_assignment_anchor(
        guidance: Dict[str, Any],
        assignment: Dict[str, Any],
    ) -> Dict[str, Any]:
        result = dict(guidance)
        result["creation_anchor_parent_id"] = guidance.get(
            "creation_anchor_parent_id",
            guidance.get("anchor_parent_id"),
        )
        if assignment.get("anchor_parent_id"):
            result["anchor_parent_id"] = assignment["anchor_parent_id"]
        if "anchor_fitness" in assignment:
            result["anchor_fitness"] = assignment["anchor_fitness"]
        return result

    def _failed_guidance(self) -> List[Dict[str, Any]]:
        return [
            {
                "guidance_id": item.get("guidance_id"),
                "idea": item.get("idea"),
                "approach_type": item.get("approach_type"),
                "uses": item.get("uses"),
                "outcome": item.get("outcome"),
                "outcome_summary": item.get("outcome_summary") or _guidance_outcome_summary(item),
            }
            for item in self.state.get("tried_guidance") or []
            if item.get("outcome") in {"no_effect", "regression", "failed"}
        ]

    def _guidance_history(self) -> List[Dict[str, Any]]:
        """Return prior guidance with deterministic outcome summaries."""
        return [
            {
                "guidance_id": item.get("guidance_id"),
                "idea": item.get("idea"),
                "approach_type": item.get("approach_type"),
                "relationship_to_current_best": item.get("relationship_to_current_best"),
                "uses": item.get("uses"),
                "max_uses": item.get("max_uses"),
                "outcome": item.get("outcome"),
                "best_fitness_at_creation": item.get("best_fitness_at_creation"),
                "outcome_summary": item.get("outcome_summary") or _guidance_outcome_summary(item),
            }
            for item in self.state.get("tried_guidance") or []
        ]

    def _completed_since_watching(self, task_count: int) -> int:
        watching = self.state.get("watching") or {}
        started = watching.get("started_task_count")
        if started is None:
            return 0
        return max(int(task_count) - int(started), 0)

    @staticmethod
    def _next_id(prefix: str, number: int) -> str:
        return f"{prefix}_{number:04d}"

    @staticmethod
    def _empty_state() -> Dict[str, Any]:
        return {
            "watching": None,
            "pending_request": None,
            "active_guidance": [],
            "tried_guidance": [],
            "task_assignments": {},
            "next_guidance_index": 1,
            "next_request_index": 1,
        }


def _record_ref(record: SuccessRecord, *, include_artifact_excerpt: bool = False) -> Dict[str, Any]:
    ref = {
        "record_id": record.id,
        "fitness": record.fitness,
        "artifact_ref": record.artifact_ref,
        "summary": record.sketch,
        "metrics": dict(record.metrics or {}),
        "profile": dict(record.profile or {}),
        "created_at": record.created_at,
    }
    if include_artifact_excerpt:
        excerpt, truncated = _read_artifact_excerpt(record.artifact_ref)
        ref["artifact_excerpt"] = excerpt
        ref["artifact_excerpt_truncated"] = truncated
    return ref


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
    required = ["idea", "description", "novelty_claim", "implementation_plan", "approach_type"]
    for key in required:
        value = item.get(key)
        if value is None or value == "" or value == []:
            return f"missing_required_field:{key}"
    text = " ".join(
        str(item.get(key, "")) for key in ["idea", "description", "novelty_claim", "approach_type"]
    ).lower()
    forbidden = ["read verifier", "read evaluator", "bypass evaluator", "modify verifier"]
    if any(term in text for term in forbidden):
        return "unsafe_guidance"
    return {
        "idea": str(item["idea"]),
        "description": str(item["description"]),
        "target_metric": str(item.get("target_metric") or "fitness"),
        "novelty_claim": str(item["novelty_claim"]),
        "current_best_assessment": str(item.get("current_best_assessment") or ""),
        "relationship_to_current_best": str(
            item.get("relationship_to_current_best") or "critically_adapt"
        ),
        "implementation_plan": [str(value) for value in item.get("implementation_plan") or []],
        "cautions": [str(value) for value in item.get("cautions") or []],
        "approach_type": str(item["approach_type"]),
        "avoid_repeating": [str(value) for value in item.get("avoid_repeating") or []],
        "max_uses": item.get("max_uses"),
    }


def _render_guidance_prompt(guidance: Dict[str, Any]) -> str:
    plan = "\n".join(f"- {item}" for item in guidance.get("implementation_plan") or [])
    cautions = "\n".join(f"- {item}" for item in guidance.get("cautions") or [])
    avoid = "\n".join(f"- {item}" for item in guidance.get("avoid_repeating") or [])
    return (
        "## META-GUIDANCE - APPLY THIS\n\n"
        "The search has stagnated and meta-guidance was requested. "
        "You MUST make a purposeful attempt to apply this guidance.\n\n"
        f"**IDEA:** {guidance.get('idea')}\n\n"
        f"**WHY THIS IS NEW:** {guidance.get('novelty_claim')}\n\n"
        f"**CURRENT BEST ASSESSMENT:** "
        f"{guidance.get('current_best_assessment') or 'Use the current best critically; do not assume its strategy is optimal.'}\n\n"
        f"**RELATIONSHIP TO CURRENT BEST:** {guidance.get('relationship_to_current_best')}\n\n"
        f"**HOW TO IMPLEMENT:**\n{guidance.get('description')}\n\n"
        f"**IMPLEMENTATION PLAN:**\n{plan or '- Apply the idea directly.'}\n\n"
        f"**TARGET METRIC:** {guidance.get('target_metric', 'fitness')}\n\n"
        f"**APPROACH TYPE:** {guidance.get('approach_type')}\n\n"
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


def _guidance_outcome_summary(guidance: Dict[str, Any]) -> Dict[str, Any]:
    outcomes = [dict(item) for item in guidance.get("outcomes") or []]
    fitness_values = [
        value
        for value in (_coerce_float(item.get("fitness")) for item in outcomes)
        if value is not None
    ]
    anchor_deltas = [
        value
        for value in (_coerce_float(item.get("delta_vs_anchor")) for item in outcomes)
        if value is not None
    ]
    valid_uses = sum(1 for item in outcomes if _is_valid_guidance_outcome(item))
    return {
        "total_uses": len(outcomes) or int(guidance.get("uses", 0) or 0),
        "valid_uses": valid_uses,
        "failed_uses": len(outcomes) - valid_uses,
        "no_effect_uses": sum(1 for item in outcomes if _is_no_effect_guidance_outcome(item)),
        "improved_anchor_uses": sum(
            1
            for item in outcomes
            if item.get("improved_anchor")
            or (_coerce_float(item.get("delta_vs_anchor")) or 0.0) > 0
        ),
        "new_best_uses": sum(1 for item in outcomes if item.get("created_new_best")),
        "best_fitness": max(fitness_values) if fitness_values else None,
        "best_delta_vs_anchor": max(anchor_deltas) if anchor_deltas else None,
    }


def _classify_guidance_outcome(guidance: Dict[str, Any]) -> str:
    outcomes = [dict(item) for item in guidance.get("outcomes") or []]
    if any(item.get("created_new_best") for item in outcomes):
        return "new_best"
    if any(
        item.get("improved_anchor") or (_coerce_float(item.get("delta_vs_anchor")) or 0.0) > 0
        for item in outcomes
    ):
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
    return bool(outcome.get("entered_success_db"))


def _is_no_effect_guidance_outcome(outcome: Dict[str, Any]) -> bool:
    return _is_valid_guidance_outcome(outcome) and bool(outcome.get("no_effect"))


def _values_equivalent(left: Any, right: Any) -> bool:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(
            _values_equivalent(left[key], right[key]) for key in left
        )
    if (
        isinstance(left, Sequence)
        and not isinstance(left, (str, bytes, bytearray))
        and isinstance(right, Sequence)
        and not isinstance(right, (str, bytes, bytearray))
    ):
        return len(left) == len(right) and all(
            _values_equivalent(left_value, right_value)
            for left_value, right_value in zip(left, right)
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

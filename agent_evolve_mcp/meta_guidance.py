"""Lightweight meta-guidance lifecycle management.

The MCP owns deterministic state and attribution.  It does not call an LLM;
the host agent generates structured guidance only when a pending request is
returned by the controller.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from agent_evolve_mcp.models import MetaEvolveConfig, SuccessRecord, utc_now_iso
from agent_evolve_mcp.storage import (
    append_jsonl,
    meta_guidance_state_path,
    meta_guidance_uses_path,
    read_json,
    write_json_atomic,
)
from agent_evolve_mcp.success_db import SuccessDB


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
        task_count: int,
        current_best_fitness: Optional[float],
    ) -> bool:
        """Return whether WATCHING has lasted long enough to request guidance."""
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
        if task_count - int(started) < self.config.watch_tasks_before_guidance:
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

        request = {
            "request_id": self._next_id("mgr", self._request_count() + 1),
            "state": "pending",
            "created_at": utc_now_iso(),
            "created_at_task_count": int(task_count),
            "task_description": task_description,
            "current_best": _record_ref(anchor),
            "progress": {
                "best_fitness_history": [],
                "completed_since_watching": self._completed_since_watching(task_count),
                "best_at_watching_start": (self.state.get("watching") or {}).get("best_fitness"),
                "current_best_fitness": anchor.fitness,
                "diagnostics": dict(diagnostics or {}),
            },
            "lineages": lineages,
            "notes_summary": {"positive": [], "negative": [], "errors": []},
            "failed_guidance": self._failed_guidance(),
            "constraints": [
                "Do not read verifier/evaluator source.",
                "Do not bypass the evaluator.",
                "Keep the benchmark output contract.",
            ],
        }
        self.state["pending_request"] = request
        self.save()
        return dict(request)

    def get_request(self, request_id: Optional[str] = None) -> Dict[str, Any]:
        """Return the pending request payload for the host agent."""
        self.load()
        pending = self.state.get("pending_request")
        if pending is None:
            return {"available": False, "reason": "no_pending_meta_guidance_request"}
        if request_id and pending.get("request_id") != request_id:
            return {"available": False, "reason": "meta_guidance_request_not_found"}
        return {"available": True, **dict(pending)}

    def commit_guidance(self, request_id: str, guidance_json: Any) -> Dict[str, Any]:
        """Validate and activate guidance generated by the host agent."""
        self.load()
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
        start_index = self._guidance_count() + 1
        for offset, item in enumerate(raw_guidance):
            validated = _validate_guidance_item(item)
            if isinstance(validated, str):
                return {"committed": False, "reason": validated}
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
                "has_success": False,
                "created_new_best": False,
            }
            active.append(guidance)
            created.append(guidance)

        self.state["active_guidance"] = active
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
        entered_success_db: bool,
        success_record_id: Optional[str],
        fitness: Optional[float],
        best_fitness_before: Optional[float],
        best_fitness_after: Optional[float],
    ) -> None:
        """Record the outcome of an attempt that used guidance."""
        self.load()
        assignment = (self.state.get("task_assignments") or {}).get(task_id)
        if not assignment:
            return
        guidance = self._active_by_id(str(assignment.get("guidance_id")))
        if guidance is None:
            return

        created_new_best = (
            entered_success_db
            and fitness is not None
            and (best_fitness_before is None or fitness > best_fitness_before)
        )
        if entered_success_db:
            guidance["has_success"] = True
        if created_new_best:
            guidance["created_new_best"] = True

        assignment.update(
            {
                "status": "recorded",
                "attempt_id": attempt_id,
                "entered_success_db": entered_success_db,
                "success_record_id": success_record_id,
                "fitness": fitness,
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
        assigned = [
            item for item in assignments.values() if item.get("guidance_id") == guidance_id
        ]
        if any(item.get("status") == "assigned" for item in assigned):
            return

        if guidance.get("created_new_best"):
            outcome = "new_best"
        elif guidance.get("has_success"):
            outcome = "useful_success"
        else:
            outcome = "failed"
        archived = {**guidance, "status": "exhausted", "outcome": outcome}
        self.state["active_guidance"] = [
            item for item in active if item.get("guidance_id") != guidance_id
        ]
        tried = list(self.state.get("tried_guidance") or [])
        tried.append(archived)
        self.state["tried_guidance"] = tried[-self.config.max_tried_guidance :]

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
        return result

    def _failed_guidance(self) -> List[Dict[str, Any]]:
        return [
            {
                "guidance_id": item.get("guidance_id"),
                "idea": item.get("idea"),
                "approach_type": item.get("approach_type"),
                "uses": item.get("uses"),
                "outcome": item.get("outcome"),
            }
            for item in self.state.get("tried_guidance") or []
            if item.get("outcome") == "failed"
        ]

    def _completed_since_watching(self, task_count: int) -> int:
        watching = self.state.get("watching") or {}
        started = watching.get("started_task_count")
        if started is None:
            return 0
        return max(int(task_count) - int(started), 0)

    def _request_count(self) -> int:
        active = self.state.get("active_guidance") or []
        tried = self.state.get("tried_guidance") or []
        pending = 1 if self.state.get("pending_request") else 0
        request_ids = {
            item.get("request_id")
            for item in [*active, *tried]
            if item.get("request_id")
        }
        return len(request_ids) + pending

    def _guidance_count(self) -> int:
        return len(self.state.get("active_guidance") or []) + len(
            self.state.get("tried_guidance") or []
        )

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
        }


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
    required = ["idea", "description", "novelty_claim", "implementation_plan", "approach_type"]
    for key in required:
        value = item.get(key)
        if value is None or value == "" or value == []:
            return f"missing_required_field:{key}"
    text = " ".join(
        str(item.get(key, ""))
        for key in ["idea", "description", "novelty_claim", "approach_type"]
    ).lower()
    forbidden = ["read verifier", "read evaluator", "bypass evaluator", "modify verifier"]
    if any(term in text for term in forbidden):
        return "unsafe_guidance"
    return {
        "idea": str(item["idea"]),
        "description": str(item["description"]),
        "target_metric": str(item.get("target_metric") or "fitness"),
        "novelty_claim": str(item["novelty_claim"]),
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
        "The search has stagnated after parameter-level exploration was increased. "
        "You MUST make a purposeful attempt to apply this guidance.\n\n"
        f"**IDEA:** {guidance.get('idea')}\n\n"
        f"**WHY THIS IS NEW:** {guidance.get('novelty_claim')}\n\n"
        f"**HOW TO IMPLEMENT:**\n{guidance.get('description')}\n\n"
        f"**IMPLEMENTATION PLAN:**\n{plan or '- Apply the idea directly.'}\n\n"
        f"**TARGET METRIC:** {guidance.get('target_metric', 'fitness')}\n\n"
        f"**APPROACH TYPE:** {guidance.get('approach_type')}\n\n"
        f"**CAUTIONS:**\n{cautions or '- Keep the benchmark output contract.'}\n\n"
        f"**AVOID REPEATING:**\n{avoid or '- No specific failed route listed.'}\n\n"
        "**ANCHOR RULES:**\n"
        "This guidance is anchored on the selected parent.\n"
        "Use the selected parent as the baseline, implementation reference, or fallback.\n"
        "You may depart substantially from the parent when required by this guidance.\n"
        "Do not force a superficial combination with inspirations if they conflict with the guidance.\n"
    )

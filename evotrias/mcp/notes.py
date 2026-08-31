"""Run-local Notes support.

This module intentionally keeps Notes small: notes live inside one run, have
only three types, and are written only from LLM-distilled JSON that MCP
validates and annotates with deterministic metadata.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
from typing import Any, Dict, Iterable, List, Optional, Sequence

from evotrias.mcp.models import AttemptRecord, SuccessRecord, utc_now_iso
from evotrias.mcp.storage import (
    append_jsonl,
    note_requests_path,
    notes_events_path,
    notes_path,
    read_json,
    read_jsonl,
    write_json_atomic,
)
from evotrias.mcp.success_db import SuccessDB

SCHEMA_VERSION = "notes_run_local"
NOTE_TYPES = {"error", "negative", "positive"}
WARMUP_TASKS = 10
NOTES_ACTIVE_START_ROUND = 11
NOTES_ACTIVE_END_ROUND = 50
DELTA_QUANTILE_START = 0.80
DELTA_QUANTILE_END = 0.60
DELTA_THRESHOLD_MIN = 0.02
DELTA_THRESHOLD_MAX = 0.05
POSITIVE_BEST_RATIO_START = 0.80
POSITIVE_BEST_RATIO_END = 1.00
POSITIVE_DUPLICATE_FITNESS_TOL = 1e-6
DEFAULT_DELTA_THRESHOLD = 0.05
FITNESS_EPS = 1e-12
POS_NEG_TARGET = 5
ERROR_PROMPT_LIMIT = 10
NOTES_ENABLED_ENV = "AGENT_EVOLVE_NOTES_ENABLED"
FALSE_ENV_VALUES = {"0", "false", "off", "no", "disabled"}


def notes_enabled() -> bool:
    """Return whether run-local Notes are enabled for this MCP process."""
    value = os.environ.get(NOTES_ENABLED_ENV)
    if value is None:
        return True
    return value.strip().lower() not in FALSE_ENV_VALUES


class NotesManager:
    """Run-local note selection, request creation, and commit validation."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.notes_file = notes_path(run_id)
        self.requests_file = note_requests_path(run_id)
        self.events_file = notes_events_path(run_id)

    def get_prompt(
        self,
        *,
        task_id: str,
        parent_id: Optional[str],
        inspiration_ids: Sequence[str],
    ) -> Dict[str, Any]:
        """Return a ready-to-inject notes prompt for one generation task."""
        collection = self._load_collection()
        notes = list(collection.get("notes") or [])
        active = [n for n in notes if n.get("note_type") in NOTE_TYPES]

        error_notes = [n for n in active if n.get("note_type") == "error"]
        error_notes = sorted(error_notes, key=lambda n: str(n.get("created_at") or ""))[
            -ERROR_PROMPT_LIMIT:
        ]

        pos_neg_notes = [n for n in active if n.get("note_type") in {"positive", "negative"}]
        anchor_ids = {item for item in [parent_id, *inspiration_ids] if item}
        matched = [n for n in pos_neg_notes if _note_matches_ids(n, anchor_ids)]
        selected_pos_neg = list(matched)

        unmatched = [n for n in pos_neg_notes if n not in selected_pos_neg]
        fill_needed = max(POS_NEG_TARGET - len(selected_pos_neg), 0)
        random_fill = _stable_sample(
            unmatched,
            limit=fill_needed,
            seed_parts=[self.run_id, task_id, parent_id or "", *sorted(inspiration_ids)],
        )
        selected_pos_neg.extend(random_fill)

        selected = [*error_notes, *selected_pos_neg]
        prompt = _render_notes_prompt(selected) if selected else ""
        counts = {
            "error": sum(1 for n in selected if n.get("note_type") == "error"),
            "positive": sum(1 for n in selected if n.get("note_type") == "positive"),
            "negative": sum(1 for n in selected if n.get("note_type") == "negative"),
            "matched_pos_neg": len(matched),
            "random_fill": len(random_fill),
        }
        return {
            "available": bool(selected),
            "prompt": prompt,
            "selected_note_ids": [str(n.get("note_id")) for n in selected],
            "counts": counts,
        }

    def summarize_for_meta_guidance(self, *, limit_per_type: int = 5) -> Dict[str, Any]:
        """Return compact run-local Notes context for meta-guidance generation."""
        collection = self._load_collection()
        notes = [
            note
            for note in collection.get("notes") or []
            if note.get("note_type") in NOTE_TYPES
        ]
        groups = {
            note_type: [
                _note_summary(note)
                for note in notes
                if note.get("note_type") == note_type
            ][-limit_per_type:]
            for note_type in ("positive", "negative", "error")
        }
        return {
            "enabled": True,
            "counts": {
                note_type: sum(1 for note in notes if note.get("note_type") == note_type)
                for note_type in ("positive", "negative", "error")
            },
            **groups,
            "errors": groups["error"],
        }

    def build_note_requests(
        self,
        *,
        attempt: AttemptRecord,
        all_attempt_rows: Sequence[Dict[str, Any]],
        db: SuccessDB,
        max_attempts: int = 3,
    ) -> List[Dict[str, Any]]:
        """Create note requests triggered by one recorded attempt."""
        existing_ids = {
            str(row.get("note_request_id"))
            for row in read_jsonl(self.requests_file)
            if row.get("note_request_id")
        }
        requests: List[Dict[str, Any]] = []
        task_attempts = [
            row for row in all_attempt_rows if str(row.get("task_id")) == attempt.task_id
        ]
        task_attempts = sorted(task_attempts, key=lambda row: str(row.get("id") or ""))
        status = _attempt_status(attempt.to_dict())
        attempt_number = _attempt_number(attempt.id)
        current_round = _task_number(attempt.task_id)
        completed_task_count = _completed_task_count_before(
            all_attempt_rows, current_task_id=attempt.task_id
        )

        if current_round <= WARMUP_TASKS:
            self._append_event(
                "note_request_skipped",
                {
                    "reason": "warmup",
                    "task_id": attempt.task_id,
                    "attempt_id": attempt.id,
                    "task_round": current_round,
                    "completed_task_count_before": completed_task_count,
                    "warmup_tasks": WARMUP_TASKS,
                },
            )
            return []

        if status == "success" and attempt_number > 1:
            failed_before = [
                row
                for row in task_attempts
                if _attempt_number(str(row.get("id"))) < attempt_number
                and _attempt_status(row) != "success"
            ]
            if failed_before:
                request = self._make_request(
                    note_type="error",
                    trigger_kind="failed_to_success",
                    attempt=attempt,
                    db=db,
                    failed_attempt_rows=failed_before,
                    relative_delta=None,
                    threshold=None,
                )
                if request["note_request_id"] not in existing_ids:
                    requests.append(request)

        if status == "success" and attempt.parent_id:
            parent = db.get(attempt.parent_id)
            child_fitness = _extract_attempt_fitness(attempt.to_dict())
            if parent is not None and child_fitness is not None:
                relative_delta = _relative_delta(child_fitness, parent.fitness)
                previous_best = _best_fitness_excluding_attempt(
                    all_attempt_rows,
                    exclude_task_id=attempt.task_id,
                    exclude_attempt_id=attempt.id,
                )
                positive_history, negative_history = _material_delta_histories(
                    all_attempt_rows=all_attempt_rows,
                    db=db,
                    exclude_task_id=attempt.task_id,
                    exclude_attempt_id=attempt.id,
                )
                delta_quantile = _delta_quantile(current_round)
                positive_threshold = _material_delta_threshold(
                    positive_history,
                    q=delta_quantile,
                )
                negative_threshold = _material_delta_threshold(
                    negative_history,
                    q=delta_quantile,
                )
                positive_gate = _positive_gate(
                    child_fitness=child_fitness,
                    previous_best=previous_best,
                    current_round=current_round,
                    parent_delta_threshold=positive_threshold,
                )
                duplicate_positive_plateau = (
                    positive_gate.get("eligible")
                    and self._positive_evidence_exists_near(child_fitness)
                )
                if (
                    positive_gate["eligible"]
                    and relative_delta >= positive_threshold
                ):
                    if duplicate_positive_plateau:
                        self._append_event(
                            "note_request_skipped",
                            {
                                "reason": "duplicate_positive_plateau",
                                "task_id": attempt.task_id,
                                "attempt_id": attempt.id,
                                "child_fitness": child_fitness,
                                "previous_best_fitness": previous_best,
                            },
                        )
                    else:
                        request = self._make_request(
                            note_type="positive",
                            trigger_kind=str(positive_gate["trigger_kind"]),
                            attempt=attempt,
                            db=db,
                            failed_attempt_rows=[],
                            relative_delta=relative_delta,
                            threshold=positive_gate["parent_delta_threshold"],
                            trigger_extra={
                                "previous_best_fitness": previous_best,
                                "positive_gate": positive_gate,
                                "positive_rule_round": current_round,
                                "delta_quantile": delta_quantile,
                                "duplicate_positive_plateau": False,
                            },
                        )
                        if request["note_request_id"] not in existing_ids:
                            requests.append(request)
                elif relative_delta <= -negative_threshold:
                    request = self._make_request(
                        note_type="negative",
                        trigger_kind="material_regression",
                        attempt=attempt,
                        db=db,
                        failed_attempt_rows=[],
                        relative_delta=relative_delta,
                        threshold=negative_threshold,
                    )
                    if request["note_request_id"] not in existing_ids:
                        requests.append(request)

        if status != "success" and attempt_number >= max_attempts:
            all_failed = task_attempts and all(
                _attempt_status(row) != "success" for row in task_attempts
            )
            if all_failed:
                request = self._make_request(
                    note_type="error",
                    trigger_kind="all_failed",
                    attempt=attempt,
                    db=db,
                    failed_attempt_rows=task_attempts,
                    relative_delta=None,
                    threshold=None,
                )
                if request["note_request_id"] not in existing_ids:
                    requests.append(request)

        for request in requests:
            append_jsonl(self.requests_file, request)
            self._append_event(
                "request_created",
                {
                    "note_request_id": request["note_request_id"],
                    "note_type": request["note_type"],
                    "task_id": attempt.task_id,
                    "attempt_id": attempt.id,
                },
            )
        return [_public_request(row) for row in requests]

    def commit_generated_note(
        self, note_request_id: str, generated_note_json: Any
    ) -> Dict[str, Any]:
        """Validate and commit one generated note."""
        request = self._get_request(note_request_id)
        if request is None:
            return {
                "committed": False,
                "retryable": False,
                "reason": "unknown note_request_id",
            }

        try:
            note = _parse_generated_note(generated_note_json)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            reason = f"invalid generated_note_json: {exc}"
            self._append_event(
                "validation_failed",
                {"note_request_id": note_request_id, "reason": reason},
            )
            return {"committed": False, "retryable": True, "reason": reason}

        error = _validate_generated_note(note)
        if error:
            self._append_event(
                "validation_failed",
                {"note_request_id": note_request_id, "reason": error},
            )
            return {"committed": False, "retryable": True, "reason": error}

        collection = self._load_collection()
        notes = list(collection.get("notes") or [])
        note_id = str(request["note_id"])
        if any(existing.get("note_id") == note_id for existing in notes):
            return {
                "committed": True,
                "note_id": note_id,
                "notes_path": str(self.notes_file),
                "already_committed": True,
            }

        committed = {
            "note_id": note_id,
            "note_type": request["note_type"],
            "title": note["title"].strip(),
            "lesson": note["lesson"].strip(),
            "actionable_guidance": note["actionable_guidance"].strip(),
            "evidence_summary": note["evidence_summary"].strip(),
            "confidence": note.get("confidence", "medium"),
            "source": dict(request.get("source") or {}),
            "trigger": dict(request.get("trigger") or {}),
            "refs": dict(request.get("refs") or {}),
            "created_at": utc_now_iso(),
        }
        notes.append(committed)
        collection["notes"] = notes
        collection["updated_at"] = utc_now_iso()
        self._save_collection(collection)
        self._append_event(
            "note_committed",
            {"note_request_id": note_request_id, "note_id": note_id},
        )
        return {
            "committed": True,
            "note_id": note_id,
            "notes_path": str(self.notes_file),
        }

    def public_requests_for_attempt(self, task_id: str, attempt_id: str) -> List[Dict[str, Any]]:
        """Return existing public requests for a task/attempt pair."""
        committed_note_ids = {
            str(note.get("note_id"))
            for note in (self._load_collection().get("notes") or [])
            if note.get("note_id")
        }
        requests = []
        for row in read_jsonl(self.requests_file):
            if row.get("task_id") != task_id or row.get("attempt_id") != attempt_id:
                continue
            if row.get("note_id") in committed_note_ids:
                continue
            requests.append(_public_request(row))
        return requests

    def _positive_evidence_exists_near(self, fitness: float) -> bool:
        """Return whether a positive note/request already covers this fitness band."""
        collection = self._load_collection()
        for note in collection.get("notes") or []:
            if note.get("note_type") != "positive":
                continue
            trigger = dict(note.get("trigger") or {})
            child_fitness = _coerce_float(trigger.get("child_fitness"))
            if child_fitness is not None and _fitness_near(child_fitness, fitness):
                return True

        for request in read_jsonl(self.requests_file):
            if request.get("note_type") != "positive":
                continue
            trigger = dict(request.get("trigger") or {})
            child_fitness = _coerce_float(trigger.get("child_fitness"))
            if child_fitness is not None and _fitness_near(child_fitness, fitness):
                return True
        return False

    def _make_request(
        self,
        *,
        note_type: str,
        trigger_kind: str,
        attempt: AttemptRecord,
        db: SuccessDB,
        failed_attempt_rows: Sequence[Dict[str, Any]],
        relative_delta: Optional[float],
        threshold: Optional[float],
        trigger_extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        parent = db.get(attempt.parent_id)
        inspirations = [record for record in (db.get(i) for i in attempt.inspiration_ids) if record]
        suffix = trigger_kind if note_type == "error" else note_type
        note_request_id = f"note_req_{attempt.task_id}_{attempt.id}_{suffix}"
        note_id = f"note_{attempt.task_id}_{attempt.id}_{suffix}"
        trigger = {
            "kind": trigger_kind,
            "relative_delta": relative_delta,
            "threshold": threshold,
            "child_fitness": _extract_attempt_fitness(attempt.to_dict()),
            "parent_fitness": parent.fitness if parent else None,
        }
        if trigger_extra:
            trigger.update(trigger_extra)
        source = {
            "run_id": self.run_id,
            "task_id": attempt.task_id,
            "attempt_id": attempt.id,
            "success_record_id": attempt.success_record_id,
            "parent_success_record_id": attempt.parent_id,
            "inspiration_success_record_ids": list(attempt.inspiration_ids),
        }
        refs = {
            "child_artifact_ref": attempt.artifact_ref,
            "parent_artifact_ref": parent.artifact_ref if parent else "",
            "inspiration_artifact_refs": [record.artifact_ref for record in inspirations],
            "verification_result_ref": attempt.verification_result_ref,
            "failed_attempt_refs": [
                str(row.get("artifact_ref") or "") for row in failed_attempt_rows
            ],
            "failed_verification_result_refs": [
                str(row.get("verification_result_ref") or "") for row in failed_attempt_rows
            ],
        }
        request = {
            "note_request_id": note_request_id,
            "note_id": note_id,
            "run_id": self.run_id,
            "task_id": attempt.task_id,
            "attempt_id": attempt.id,
            "note_type": note_type,
            "trigger": trigger,
            "source": source,
            "refs": refs,
            "analysis_prompt": _analysis_prompt(
                note_type=note_type,
                trigger_kind=trigger_kind,
                attempt=attempt,
                parent=parent,
                inspirations=inspirations,
                failed_attempt_rows=failed_attempt_rows,
                relative_delta=relative_delta,
                threshold=threshold,
            ),
            "expected_schema": {
                "required": [
                    "title",
                    "lesson",
                    "actionable_guidance",
                    "evidence_summary",
                    "confidence",
                ],
                "confidence_values": ["low", "medium", "high"],
            },
            "created_at": utc_now_iso(),
        }
        return request

    def _get_request(self, note_request_id: str) -> Optional[Dict[str, Any]]:
        match = None
        for row in read_jsonl(self.requests_file):
            if row.get("note_request_id") == note_request_id:
                match = row
        return match

    def _load_collection(self) -> Dict[str, Any]:
        data = read_json(
            self.notes_file,
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": self.run_id,
                "created_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
                "config": {
                    "warmup_tasks": WARMUP_TASKS,
                    "notes_active_start_round": NOTES_ACTIVE_START_ROUND,
                    "notes_active_end_round": NOTES_ACTIVE_END_ROUND,
                    "delta_quantile_start": DELTA_QUANTILE_START,
                    "delta_quantile_end": DELTA_QUANTILE_END,
                    "delta_threshold_min": DELTA_THRESHOLD_MIN,
                    "delta_threshold_max": DELTA_THRESHOLD_MAX,
                    "positive_best_ratio_start": POSITIVE_BEST_RATIO_START,
                    "positive_best_ratio_end": POSITIVE_BEST_RATIO_END,
                    "max_error_notes": ERROR_PROMPT_LIMIT,
                    "max_positive_negative_random_fill": POS_NEG_TARGET,
                },
                "notes": [],
            },
        )
        data.setdefault("schema_version", SCHEMA_VERSION)
        data.setdefault("run_id", self.run_id)
        data.setdefault("notes", [])
        return data

    def _save_collection(self, collection: Dict[str, Any]) -> None:
        write_json_atomic(self.notes_file, collection)

    def _append_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        append_jsonl(
            self.events_file,
            {
                "event_type": event_type,
                "run_id": self.run_id,
                "created_at": utc_now_iso(),
                **payload,
            },
        )


def _public_request(request: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "note_request_id": request["note_request_id"],
        "note_type": request["note_type"],
        "analysis_prompt": request["analysis_prompt"],
        "expected_schema": request["expected_schema"],
    }


def _note_matches_ids(note: Dict[str, Any], ids: Iterable[str]) -> bool:
    anchor_ids = {str(item) for item in ids if item}
    if not anchor_ids:
        return False
    source = dict(note.get("source") or {})
    direct = {
        str(source.get("success_record_id") or ""),
        str(source.get("parent_success_record_id") or ""),
    }
    inspirations = {str(item) for item in source.get("inspiration_success_record_ids") or []}
    return bool(anchor_ids & (direct | inspirations))


def _stable_sample(
    items: Sequence[Dict[str, Any]], *, limit: int, seed_parts: Sequence[str]
) -> List[Dict[str, Any]]:
    if limit <= 0 or not items:
        return []
    seed_text = "|".join(seed_parts)
    seed = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    pool = list(items)
    rng.shuffle(pool)
    return pool[:limit]


def _render_notes_prompt(notes: Sequence[Dict[str, Any]]) -> str:
    groups = {
        "error": [n for n in notes if n.get("note_type") == "error"],
        "positive": [n for n in notes if n.get("note_type") == "positive"],
        "negative": [n for n in notes if n.get("note_type") == "negative"],
    }
    lines = [
        "## Run-local evolution notes",
        "",
        "The following notes are distilled from earlier attempts in this same run.",
        "Use them as practical guidance, not as hard proof. Prefer the selected parent",
        "and inspirations, but take these notes into account when adapting them.",
        "Treat positive notes as conditional hints, not proof that the same",
        "strategy should be repeated. Prefer a genuinely new variant when prior",
        "notes already describe the same fitness plateau.",
        "Treat negative notes as conditional risk warnings about the recorded",
        "implementation and parameter choices, not as global bans on a whole",
        "strategy family. A strategy may still be useful with different parameters,",
        "fallbacks, acceptance checks, or integration points.",
    ]
    headings = {
        "error": "### Error notes: avoid repeated failed patterns",
        "positive": "### Positive notes: patterns that previously improved fitness",
        "negative": "### Negative notes: patterns that previously degraded fitness",
    }
    for note_type in ("error", "positive", "negative"):
        bucket = groups[note_type]
        if not bucket:
            continue
        lines.extend(["", headings[note_type], ""])
        for idx, note in enumerate(bucket, start=1):
            source = dict(note.get("source") or {})
            trigger = dict(note.get("trigger") or {})
            source_label = _source_label(source, trigger)
            lines.extend(
                [
                    f"{idx}. [{source_label}]",
                    f"Title: {_short(note.get('title'))}",
                    f"Lesson: {_short(note.get('lesson'))}",
                    f"Guidance: {_short(note.get('actionable_guidance'))}",
                ]
            )
    return "\n".join(lines).strip()


def _note_summary(note: Dict[str, Any]) -> Dict[str, Any]:
    trigger = dict(note.get("trigger") or {})
    return {
        "note_id": str(note.get("note_id") or ""),
        "note_type": str(note.get("note_type") or ""),
        "title": _short(note.get("title"), 120),
        "lesson": _short(note.get("lesson"), 240),
        "actionable_guidance": _short(note.get("actionable_guidance"), 240),
        "confidence": str(note.get("confidence") or "medium"),
        "trigger": {
            "kind": trigger.get("kind"),
            "relative_delta": trigger.get("relative_delta"),
            "child_fitness": trigger.get("child_fitness"),
            "parent_fitness": trigger.get("parent_fitness"),
        },
    }


def _source_label(source: Dict[str, Any], trigger: Dict[str, Any]) -> str:
    task_id = source.get("task_id") or "unknown_task"
    attempt_id = source.get("attempt_id") or "unknown_attempt"
    delta = trigger.get("relative_delta")
    parent = source.get("parent_success_record_id")
    if isinstance(delta, (int, float)) and parent:
        return f"{task_id} / {attempt_id}, {delta * 100:+.1f}% vs parent {parent}"
    return f"{task_id} / {attempt_id}"


def _analysis_prompt(
    *,
    note_type: str,
    trigger_kind: str,
    attempt: AttemptRecord,
    parent: Optional[SuccessRecord],
    inspirations: Sequence[SuccessRecord],
    failed_attempt_rows: Sequence[Dict[str, Any]],
    relative_delta: Optional[float],
    threshold: Optional[float],
) -> str:
    common = [
        "You are generating one run-local evolution note.",
        "",
        "The note must summarize one useful experience from this task.",
        "Use the referenced artifacts and verification results as evidence.",
        "Do not copy long code blocks, full logs, or full tracebacks.",
        "Do not include your reasoning process, step-by-step analysis, or chain-of-thought in the note.",
        "The final note should contain only the distilled conclusion, actionable guidance, and a very short evidence summary.",
        "Do not decide whether the note is needed; MCP has already decided that.",
        "Treat this as one empirical case, not proof that a strategy family is generally good or bad.",
        "Keep attribution scoped to what this single comparison can support.",
        "If several changes happened together, do not claim one mechanism is the sole cause unless the evidence clearly isolates it.",
        "Verifier success proves that the submitted artifact was valid; it does not prove that every advertised internal algorithm or branch executed.",
        "Inspect the child artifact and verification evidence for primary, fallback, dispatch, and selector control flow before attributing the outcome to a particular strategy.",
        "If the executed branch cannot be established, attribute the outcome to the artifact or selector as a whole, state that the internal strategy is unverified, and lower confidence.",
        "Never claim that a solver reached an optimum or that a particular strategy succeeded unless the evaluated result can be traced to that executed branch.",
        "When parameters, seeds, acceptance checks, fallback choices, or integration points matter, name those conditions explicitly.",
        "Do not turn one attempt into a universal rule about a whole strategy family.",
        "Return exactly one JSON object that matches the schema.",
        "",
    ]
    materials = [
        f"Task: {attempt.task_id}",
        f"Attempt: {attempt.id}",
        f"- child implementation: {attempt.artifact_ref}",
        f"- child verification result: {attempt.verification_result_ref}",
        f"- parent implementation: {parent.artifact_ref if parent else ''}",
        f"- parent fitness / metrics: {parent.fitness if parent else None} / {parent.metrics if parent else {}}",
        f"- inspirations: {_format_inspirations(inspirations)}",
    ]
    if note_type == "positive":
        return "\n".join(
            [
                *common,
                "Note type: positive",
                f"Trigger: child fitness improved over parent by {_pct(relative_delta)}; threshold is {_pct(threshold)}.",
                "",
                "Materials:",
                *materials,
                "",
                "Your job:",
                "1. Compare child against parent.",
                "2. Identify the concrete implementation change most likely responsible for the improvement.",
                "3. If the idea appears to come from an inspiration, mention which one and how it was adapted.",
                "4. Write a concise note that future tasks can use.",
                '5. Be careful with causality: use "likely" when the evidence is suggestive rather than definitive.',
                "6. If the improvement may depend on specific parameters, seeds, fallback logic, or acceptance checks, state that condition instead of praising the broad method unconditionally.",
                "7. Do not write that a broad method is proven superior unless the evidence isolates it from parameters and implementation details.",
                "8. The returned JSON must not include the comparison process; keep only the conclusion.",
                "",
                _schema_text(),
            ]
        )
    if note_type == "negative":
        return "\n".join(
            [
                *common,
                "Note type: negative",
                f"Trigger: child fitness dropped below parent by {_pct(relative_delta)}; threshold is {_pct(threshold)}.",
                "",
                "Materials:",
                *materials,
                "",
                "Your job:",
                "1. Compare child against parent.",
                "2. Identify the concrete implementation detail, parameter choice, or integration pattern most likely responsible for the regression.",
                "3. Check whether the regression came from misusing an inspiration, over-combining strategies, or breaking a useful parent mechanism.",
                "4. Write a concise caution note for future tasks.",
                "5. Do not overgeneralize: describe the condition under which the risk appeared.",
                "6. Do not write that a method or strategy should never be used just because this attempt failed.",
                "7. If a method might still work with different parameters, a safer acceptance check, or a different integration point, say that the risk is limited to the observed implementation.",
                '8. Prefer wording like "avoid this unguarded variant" or "use only if it improves validation" over blanket bans; never recommend a silent fallback that would hide whether the claimed strategy executed.',
                "9. If the child changed both the strategy and its parameters, attribute risk to the observed configuration rather than condemning the whole strategy.",
                "10. The returned JSON must not include the diagnostic process; keep only the conclusion.",
                "",
                _schema_text(),
            ]
        )

    failed_refs = [str(row.get("artifact_ref") or "") for row in failed_attempt_rows]
    failed_result_refs = [
        str(row.get("verification_result_ref") or "") for row in failed_attempt_rows
    ]
    if trigger_kind == "all_failed":
        title = "Trigger: all attempts for this task failed verification."
        jobs = [
            "1. Read the failed implementations and verification errors.",
            "2. Identify the shared failure pattern, or the sequence of different failed fixes.",
            "3. Write one concise note that helps future tasks avoid this failure.",
            "4. Do not paste raw logs. Summarize the failure reason and the preventive action.",
            "5. The returned JSON must not include the debugging process; keep only the conclusion.",
        ]
    else:
        title = f"Trigger: attempt {attempt.id} succeeded after earlier failed attempts in the same task."
        jobs = [
            "1. Compare the failed attempts with the successful attempt.",
            "2. Identify the key fix that changed failure into success.",
            "3. Write one concise note that future tasks can use as a checklist item.",
            "4. Focus on the failure mode and the repair, not on the full implementation.",
            "5. The returned JSON must not include the comparison process; keep only the conclusion.",
        ]
    return "\n".join(
        [
            *common,
            "Note type: error",
            title,
            "",
            "Materials:",
            *materials,
            f"- failed attempts: {failed_refs}",
            f"- failed verification results: {failed_result_refs}",
            "",
            "Your job:",
            *jobs,
            "",
            _schema_text(),
        ]
    )


def _schema_text() -> str:
    return (
        "Return JSON schema:\n"
        "{\n"
        '  "title": "short imperative title",\n'
        '  "lesson": "one concise conclusion distilled from the evidence; no reasoning process",\n'
        '  "actionable_guidance": "one short instruction about what a future generation should do or avoid",\n'
        '  "evidence_summary": "very brief factual evidence; no analysis process",\n'
        '  "confidence": "low|medium|high"\n'
        "}"
    )


def _format_inspirations(records: Sequence[SuccessRecord]) -> str:
    return json.dumps(
        [
            {
                "success_record_id": r.id,
                "artifact_ref": r.artifact_ref,
                "fitness": r.fitness,
                "metrics": r.metrics,
            }
            for r in records
        ],
        ensure_ascii=False,
    )


def _parse_generated_note(generated_note_json: Any) -> Dict[str, Any]:
    if isinstance(generated_note_json, str):
        return json.loads(generated_note_json)
    if isinstance(generated_note_json, dict):
        return dict(generated_note_json)
    raise ValueError("generated_note_json must be an object or JSON object string.")


def _validate_generated_note(note: Dict[str, Any]) -> Optional[str]:
    required = ["title", "lesson", "actionable_guidance", "evidence_summary"]
    for key in required:
        if not str(note.get(key) or "").strip():
            return f"missing required field: {key}"
    if len(str(note["title"])) > 120:
        return "title too long"
    if len(str(note["lesson"])) > 400:
        return "lesson too long"
    if len(str(note["actionable_guidance"])) > 400:
        return "actionable_guidance too long"
    if len(str(note["evidence_summary"])) > 500:
        return "evidence_summary too long"
    confidence = str(note.get("confidence") or "medium")
    if confidence not in {"low", "medium", "high"}:
        return "confidence must be low, medium, or high"
    joined = "\n".join(str(note.get(key) or "") for key in required).lower()
    banned = [
        "```",
        "traceback (most recent call last)",
        "chain-of-thought",
        "step-by-step",
        "reasoning process",
        "my reasoning",
        "first, i",
        "then, i",
    ]
    if any(token in joined for token in banned):
        return "note must not contain code blocks, long logs, or reasoning process"
    return None


def _attempt_status(row: Dict[str, Any]) -> str:
    verification = dict(row.get("verification_result") or {})
    return str(verification.get("status") or "").lower()


def _extract_attempt_fitness(row: Dict[str, Any]) -> Optional[float]:
    verification = dict(row.get("verification_result") or {})
    value = verification.get("fitness")
    return _coerce_float(value)


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _completed_task_count_before(
    all_attempt_rows: Sequence[Dict[str, Any]],
    current_task_id: Optional[str] = None,
) -> int:
    task_ids = {
        str(row.get("task_id"))
        for row in all_attempt_rows
        if row.get("task_id") and str(row.get("task_id")) != str(current_task_id)
    }
    return len(task_ids)


def _best_fitness_excluding_attempt(
    all_attempt_rows: Sequence[Dict[str, Any]],
    *,
    exclude_task_id: Optional[str],
    exclude_attempt_id: Optional[str],
) -> Optional[float]:
    values: List[float] = []
    for row in all_attempt_rows:
        if _same_attempt(
            row,
            task_id=exclude_task_id,
            attempt_id=exclude_attempt_id,
        ):
            continue
        if _attempt_status(row) != "success":
            continue
        fitness = _extract_attempt_fitness(row)
        if fitness is not None:
            values.append(fitness)
    return max(values) if values else None


def _same_attempt(
    row: Dict[str, Any],
    *,
    task_id: Optional[str],
    attempt_id: Optional[str],
) -> bool:
    if not task_id or not attempt_id:
        return False
    return str(row.get("task_id")) == str(task_id) and str(row.get("id")) == str(attempt_id)


def _positive_gate(
    *,
    child_fitness: float,
    previous_best: Optional[float],
    current_round: int,
    parent_delta_threshold: float,
) -> Dict[str, Any]:
    best_ratio_threshold = _positive_best_ratio_threshold(current_round)
    if previous_best is None:
        return {
            "eligible": True,
            "phase": "active",
            "trigger_kind": "material_improvement_new_best",
            "reason": "no_previous_best",
            "parent_delta_threshold": parent_delta_threshold,
            "best_ratio_threshold": best_ratio_threshold,
        }
    if child_fitness > previous_best + FITNESS_EPS:
        return {
            "eligible": True,
            "phase": "active",
            "trigger_kind": "material_improvement_new_best",
            "reason": "new_global_best",
            "parent_delta_threshold": parent_delta_threshold,
            "best_ratio_threshold": best_ratio_threshold,
        }
    required_fitness = previous_best * best_ratio_threshold
    eligible = child_fitness + FITNESS_EPS >= required_fitness
    return {
        "eligible": eligible,
        "phase": "active",
        "trigger_kind": "near_best_material_improvement",
        "reason": "near_previous_best" if eligible else "below_best_ratio_threshold",
        "parent_delta_threshold": parent_delta_threshold,
        "best_ratio_threshold": best_ratio_threshold,
        "required_fitness": required_fitness,
    }


def _positive_best_ratio_threshold(current_round: int) -> float:
    t = _linear_progress(
        current_round,
        start=NOTES_ACTIVE_START_ROUND,
        end=NOTES_ACTIVE_END_ROUND,
    )
    return _lerp(POSITIVE_BEST_RATIO_START, POSITIVE_BEST_RATIO_END, t)


def _delta_quantile(current_round: int) -> float:
    t = _linear_progress(
        current_round,
        start=NOTES_ACTIVE_START_ROUND,
        end=NOTES_ACTIVE_END_ROUND,
    )
    return _lerp(DELTA_QUANTILE_START, DELTA_QUANTILE_END, t)


def _material_delta_threshold(values: Sequence[float], *, q: float) -> float:
    raw = _quantile(values, q) if values else DEFAULT_DELTA_THRESHOLD
    return max(DELTA_THRESHOLD_MIN, min(raw, DELTA_THRESHOLD_MAX))


def _material_delta_histories(
    *,
    all_attempt_rows: Sequence[Dict[str, Any]],
    db: SuccessDB,
    exclude_task_id: Optional[str],
    exclude_attempt_id: Optional[str],
) -> tuple[List[float], List[float]]:
    positive: List[float] = []
    negative: List[float] = []
    for row in all_attempt_rows:
        if _same_attempt(
            row,
            task_id=exclude_task_id,
            attempt_id=exclude_attempt_id,
        ):
            continue
        if _attempt_status(row) != "success":
            continue
        parent_id = row.get("parent_id")
        parent = db.get(str(parent_id)) if parent_id else None
        fitness = _extract_attempt_fitness(row)
        if parent is None or fitness is None:
            continue
        delta = _relative_delta(fitness, parent.fitness)
        if delta > 0:
            positive.append(delta)
        elif delta < 0:
            negative.append(abs(delta))
    return positive, negative


def _linear_progress(value: int, *, start: int, end: int) -> float:
    if end <= start:
        return 1.0
    return max(0.0, min((value - start) / (end - start), 1.0))


def _lerp(start: float, end: float, t: float) -> float:
    return start + (end - start) * t


def _fitness_near(a: float, b: float) -> bool:
    scale = max(abs(a), abs(b), 1.0)
    return abs(a - b) <= scale * POSITIVE_DUPLICATE_FITNESS_TOL


def _attempt_number(attempt_id: str) -> int:
    text = str(attempt_id)
    if "_" in text:
        text = text.rsplit("_", 1)[-1]
    try:
        return int(text)
    except ValueError:
        return 1


def _task_number(task_id: str) -> int:
    text = str(task_id)
    if "_" in text:
        text = text.rsplit("_", 1)[-1]
    try:
        return int(text)
    except ValueError:
        return 1


def _relative_delta(child_fitness: float, parent_fitness: float) -> float:
    denom = max(abs(parent_fitness), 1e-6)
    return (child_fitness - parent_fitness) / denom


def _quantile(values: Sequence[float], q: float) -> float:
    if not values:
        return DEFAULT_DELTA_THRESHOLD
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - pos) + ordered[hi] * (pos - lo)


def _pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"


def _short(value: Any, limit: int = 280) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."

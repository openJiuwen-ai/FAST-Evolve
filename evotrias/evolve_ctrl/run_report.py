"""Deterministic run report generation for completed evolution runs.

The report writer deliberately avoids LLM-authored summaries.  It rebuilds the
run view from persisted controller state and writes stable Markdown/JSON
artifacts under ``runs/<run_id>/`` for human review and later analysis.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from evotrias.db import paths
from evotrias.db.attempts import AttemptRecord, Attempts
from evotrias.db.meta_guidance_events import MetaGuidanceEvents
from evotrias.db.meta_guidance_state import MetaGuidanceState
from evotrias.db.run_state import RunState
from evotrias.evolve_ctrl.lineage_tree import LineageTree
from evotrias.evolve_ctrl.success_pool import SuccessPool, SuccessRecord
from evotrias.logger import logger
from evotrias.utils.io import write_json_atomic


def write_run_report(
    run_id: str,
    db: SuccessPool,
    lineage_tree: LineageTree,
    run_state: RunState,
) -> Dict[str, str]:
    """Write Markdown and JSON summaries for one run.

    Returns only path-like refs so public MCP calls stay compact.
    """
    lineage_tree.rebuild(db)
    attempts = list(Attempts(run_id))
    records = db.get_all()

    report_dir = paths.public_run_dir(run_id)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / paths.RUN_SUMMARY_MD
    summary_path = report_dir / paths.RUN_SUMMARY_JSON

    summary = _build_summary(
        run_id=run_id,
        records=records,
        attempts=attempts,
        lineage_tree=lineage_tree,
        run_state=run_state,
        report_ref=_as_ref(report_path),
        summary_ref=_as_ref(summary_path),
    )

    write_json_atomic(summary_path, summary)
    report_path.write_text(_render_markdown(summary), encoding="utf-8")
    logger.info(f"run {run_id}: report written to {_as_ref(report_path)} and {_as_ref(summary_path)}")
    return {
        "report_ref": _as_ref(report_path),
        "summary_ref": _as_ref(summary_path),
    }


def _build_summary(
    run_id: str,
    records: List[SuccessRecord],
    attempts: List[AttemptRecord],
    lineage_tree: LineageTree,
    run_state: RunState,
    report_ref: str,
    summary_ref: str,
) -> Dict[str, Any]:
    best = max(records, key=lambda record: record.fitness) if records else None
    top_records = sorted(records, key=lambda record: record.fitness, reverse=True)[:5]
    failed_attempts = [attempt for attempt in attempts if not attempt.in_success_pool]
    success_count = len(records)
    attempt_count = len(attempts)

    return {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "report_ref": report_ref,
        "summary_ref": summary_ref,
        "status": run_state.search_state.value,
        "stop_reason": run_state.stop_reason or "manual_finalize",
        "diagnostics": dict(run_state.diagnostics or {}),
        "overview": {
            "started_at": run_state.created_at,
            "updated_at": run_state.updated_at,
            "total_attempts": attempt_count,
            "successful_records": success_count,
            "failed_attempts": len(failed_attempts),
            "success_rate": success_count / attempt_count if attempt_count else 0.0,
            "best_record_id": best.id if best else None,
            "best_task_id": best.task_id if best else None,
            "best_fitness": best.fitness if best else None,
            "best_artifact_ref": best.artifact_ref if best else None,
        },
        "best_result": _record_summary(best) if best else None,
        "top_results": [_record_summary(record) for record in top_records],
        "fitness_progression": _fitness_progression(records),
        "lineage_statistics": _lineage_statistics(lineage_tree),
        "lineage_mermaid": _lineage_mermaid(records, lineage_tree, best.id if best else None),
        "meta_evolve": _meta_evolve_summary(
            run_id,
            enabled=(run_state.diagnostics.get("meta_guidance") or {}).get("enabled"),
        ),
        "attempts": [_attempt_summary(attempt) for attempt in attempts],
        "deterministic_observations": _deterministic_observations(records, lineage_tree, best),
    }


def _record_summary(record: Optional[SuccessRecord]) -> Optional[Dict[str, Any]]:
    if record is None:
        return None
    return {
        "id": record.id,
        "task_id": record.task_id,
        "fitness": record.fitness,
        "generation": record.generation,
        "parent_id": record.parent_id,
        "artifact_ref": record.artifact_ref,
        "verification_result_ref": record.verification_result_ref,
        "created_at": record.created_at,
        "sketch": record.sketch,
        "metrics": dict(record.metrics or {}),
        "profile": dict(record.profile or {}),
    }


def _attempt_summary(attempt: AttemptRecord) -> Dict[str, Any]:
    result = dict(attempt.verification_result or {})
    return {
        "id": attempt.id,
        "task_id": attempt.task_id,
        "status": result.get("status"),
        "fitness": result.get("fitness"),
        "in_success_pool": attempt.in_success_pool,
        "success_record_id": attempt.success_record_id,
        "parent_id": attempt.parent_id,
        "meta_guidance_id": attempt.meta_guidance_id,
        "anchor_parent_id": attempt.anchor_parent_id,
        "generation": attempt.generation,
        "artifact_ref": attempt.artifact_ref,
        "verification_result_ref": attempt.verification_result_ref,
        "created_at": attempt.created_at,
    }


def _fitness_progression(records: List[SuccessRecord]) -> List[Dict[str, Any]]:
    progression = []
    running_best = None
    for index, record in enumerate(records, start=1):
        running_best = record.fitness if running_best is None else max(running_best, record.fitness)
        progression.append(
            {
                "order": index,
                "record_id": record.id,
                "task_id": record.task_id,
                "generation": record.generation,
                "fitness": record.fitness,
                "running_best": running_best,
            }
        )
    return progression


def _lineage_statistics(lineage_tree: LineageTree) -> List[Dict[str, Any]]:
    stats = []
    for root_id, lineage_stats in sorted(lineage_tree.get_all_lineage_stats().items()):
        stats.append(asdict(lineage_stats))
    return stats


def _meta_evolve_summary(run_id: str, *, enabled: Optional[bool] = None) -> Dict[str, Any]:
    state = MetaGuidanceState(run_id)
    guidance = [*state.active_guidance, *state.tried_guidance]
    return {
        "enabled": enabled,
        "watching": asdict(state.watching) if state.watching else None,
        "has_pending_request": state.pending_request is not None,
        "active_guidance_count": len(state.active_guidance),
        "tried_guidance_count": len(state.tried_guidance),
        "assigned_task_count": len(state.task_assignments),
        "use_events": sum(1 for e in MetaGuidanceEvents(run_id) if e.event_type in {"assigned", "recorded"}),
        "new_best_from_guidance": sum(1 for item in guidance if item.created_new_best),
        "guidance": [
            {
                "guidance_id": item.guidance_id,
                "idea": item.idea,
                "uses": item.uses,
                "max_uses": item.max_uses,
                "status": item.status,
                "outcome": item.outcome,
                "anchor_parent_id": item.anchor_parent_id,
            }
            for item in guidance
        ],
    }


def _deterministic_observations(
    records: List[SuccessRecord],
    lineage_tree: LineageTree,
    best: Optional[SuccessRecord],
) -> List[str]:
    observations = []
    if best is not None:
        observations.append(
            f"Best record {best.id} reached fitness {_format_float(best.fitness)} "
            f"at generation {best.generation}."
        )

    lineage_stats = sorted(
        lineage_tree.get_all_lineage_stats().values(),
        key=lambda item: (item.size, item.best_fitness),
        reverse=True,
    )
    if lineage_stats:
        largest = lineage_stats[0]
        observations.append(
            f"Largest lineage root {largest.root_id} contains {largest.size} "
            f"successful records with best fitness {_format_float(largest.best_fitness)}."
        )
    if len(lineage_stats) > 1:
        observations.append(f"Run contains {len(lineage_stats)} independent success lineages.")
    if records:
        observations.append(
            f"Recorded fitness range is {_format_float(min(record.fitness for record in records))} "
            f"to {_format_float(max(record.fitness for record in records))}."
        )
    return observations


def _lineage_mermaid(
    records: List[SuccessRecord],
    lineage_tree: LineageTree,
    best_id: Optional[str],
) -> str:
    if not records:
        return 'flowchart TB\n    empty["No successful records"]\n'

    sorted_records = sorted(
        records, key=lambda record: (record.generation, record.created_at, record.id)
    )
    record_ids = {record.id for record in sorted_records}
    fitness_bucket = _fitness_buckets(sorted_records)

    lines = ["flowchart TB", ""]
    generations: Dict[int, List[SuccessRecord]] = {}
    for record in sorted_records:
        generations.setdefault(record.generation, []).append(record)

    for generation in sorted(generations):
        title = "Initial Tasks" if generation == 0 else f"Gen{generation}"
        lines.append(f"    subgraph {_quote_mermaid(title)}")
        for record in generations[generation]:
            node_id = _node_id(record.id)
            root_id = lineage_tree.get_lineage_root(record.id)
            label_parts = [
                f"{_best_prefix(record.id, best_id)}{record.task_id or record.id}",
                f"fitness: {_format_float(record.fitness)}",
            ]
            if record.parent_id is None or record.parent_id not in record_ids:
                stats = lineage_tree.get_lineage_stats(root_id)
                if stats is not None:
                    label_parts.append(f"lineage size: {stats.size}")
            label = "<br/>".join(_escape_mermaid_label(part) for part in label_parts)
            lines.append(f'        {node_id}["{label}"]')
        lines.append("    end")
        lines.append("")

    lines.append("    %% Parent-child relationships")
    for record in sorted_records:
        if record.parent_id and record.parent_id in record_ids:
            lines.append(f"    {_node_id(record.parent_id)} --> {_node_id(record.id)}")
    lines.append("")

    lines.append("    %% Styles: red=lower fitness, orange=middle, green=higher, gold border=best")
    for record in sorted_records:
        color = fitness_bucket.get(record.id, "#fed7aa")
        stroke = "#92400e"
        width = "1px"
        if record.id == best_id:
            color = "#fde68a"
            stroke = "#a16207"
            width = "4px"
        lines.append(
            f"    style {_node_id(record.id)} fill:{color},stroke:{stroke},"
            f"stroke-width:{width},color:#111827"
        )
    return "\n".join(lines) + "\n"


def _fitness_buckets(records: List[SuccessRecord]) -> Dict[str, str]:
    ordered = sorted(records, key=lambda record: record.fitness)
    total = len(ordered)
    buckets = {}
    for index, record in enumerate(ordered):
        percentile = (index + 1) / max(total, 1)
        if percentile <= 0.30:
            buckets[record.id] = "#fecaca"
        elif percentile <= 0.70:
            buckets[record.id] = "#fed7aa"
        else:
            buckets[record.id] = "#bbf7d0"
    return buckets


def _render_markdown(summary: Dict[str, Any]) -> str:
    overview = summary["overview"]
    lines = [
        f"# Evolution Run Summary: {summary['run_id']}",
        "",
        "## Overview",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Run ID | `{summary['run_id']}` |",
        f"| Status | {summary['status']} |",
        f"| Stop Reason | {summary['stop_reason']} |",
        f"| Started At | {overview['started_at']} |",
        f"| Updated At | {overview['updated_at']} |",
        f"| Total Attempts | {overview['total_attempts']} |",
        f"| Successful Records | {overview['successful_records']} |",
        f"| Failed Attempts | {overview['failed_attempts']} |",
        f"| Success Rate | {_format_percent(overview['success_rate'])} |",
        f"| Best Fitness | {_format_optional_float(overview['best_fitness'])} |",
        f"| Best Task | {overview['best_task_id'] or ''} |",
        f"| Best Artifact | `{overview['best_artifact_ref'] or ''}` |",
        "",
        "## Run Control Diagnostics",
        "",
    ]
    lines.extend(_diagnostics_table(summary["diagnostics"], summary["stop_reason"]))
    lines.extend(
        [
            "",
            "## Lineage Graph",
            "",
            "```mermaid",
            summary["lineage_mermaid"].rstrip(),
            "```",
            "",
            "## Top Results",
            "",
        ]
    )
    lines.extend(_top_results_table(summary["top_results"]))
    lines.extend(["", "## Fitness Progression", ""])
    lines.extend(_fitness_progression_table(summary["fitness_progression"]))
    lines.extend(["", "## Lineage Statistics", ""])
    lines.extend(_lineage_stats_table(summary["lineage_statistics"]))
    lines.extend(["", "## Meta-Evolve", ""])
    lines.extend(_meta_evolve_table(summary["meta_evolve"]))
    lines.extend(["", "## Attempts", ""])
    lines.extend(_attempts_table(summary["attempts"]))
    lines.extend(["", "## Deterministic Observations", ""])
    observations = summary["deterministic_observations"]
    if observations:
        lines.extend(f"- {item}" for item in observations)
    else:
        lines.append("- No successful records were available for deterministic observations.")
    lines.append("")
    return "\n".join(lines)


def _diagnostics_table(diagnostics: Dict[str, Any], stop_reason: str) -> List[str]:
    if not diagnostics:
        return ["No run-control diagnostics were recorded."]
    preferred = [
        "state",
        "stop_reason",
        "plateau_count",
        "patience",
        "window",
        "fitness_improvement",
        "fitness_improvement_threshold",
        "plateau_detected",
        "current_best_fitness",
        "current_diversity",
        "total_success_tracked",
        "latest_success_is_new_best",
    ]
    lines = ["| Signal | Value |", "|---|---|", f"| final_stop_reason | {stop_reason} |"]
    emitted = set()
    for key in preferred:
        if key in diagnostics:
            lines.append(f"| {key} | {_format_value(diagnostics[key])} |")
            emitted.add(key)
    for key in sorted(set(diagnostics) - emitted):
        lines.append(f"| {key} | {_format_value(diagnostics[key])} |")
    return lines


def _top_results_table(records: List[Dict[str, Any]]) -> List[str]:
    lines = [
        "| Rank | Task | Fitness | Generation | Parent | Artifact |",
        "|---:|---|---:|---:|---|---|",
    ]
    if not records:
        lines.append("| - | - | - | - | - | - |")
        return lines
    for rank, record in enumerate(records, start=1):
        lines.append(
            f"| {rank} | {record.get('task_id') or record.get('id')} | "
            f"{_format_optional_float(record.get('fitness'))} | {record.get('generation')} | "
            f"{record.get('parent_id') or ''} | `{record.get('artifact_ref') or ''}` |"
        )
    return lines


def _fitness_progression_table(rows: List[Dict[str, Any]]) -> List[str]:
    lines = ["| Order | Task | Generation | Fitness | Running Best |", "|---:|---|---:|---:|---:|"]
    if not rows:
        lines.append("| - | - | - | - | - |")
        return lines
    for row in rows:
        lines.append(
            f"| {row['order']} | {row.get('task_id') or row.get('record_id')} | "
            f"{row['generation']} | {_format_float(row['fitness'])} | {_format_float(row['running_best'])} |"
        )
    return lines


def _lineage_stats_table(rows: List[Dict[str, Any]]) -> List[str]:
    lines = [
        "| Root | Size | Best Fitness | Best Record | Max Depth | Share |",
        "|---|---:|---:|---|---:|---:|",
    ]
    if not rows:
        lines.append("| - | - | - | - | - | - |")
        return lines
    for row in rows:
        lines.append(
            f"| {row['root_id']} | {row['size']} | {_format_float(row['best_fitness'])} | "
            f"{row['best_record_id']} | {row['max_depth']} | {_format_percent(row['share'])} |"
        )
    return lines


def _meta_evolve_table(meta: Dict[str, Any]) -> List[str]:
    lines = [
        "| Field | Value |",
        "|---|---|",
        f"| enabled | {meta.get('enabled')} |",
        f"| has_pending_request | {meta.get('has_pending_request')} |",
        f"| active_guidance_count | {meta.get('active_guidance_count')} |",
        f"| tried_guidance_count | {meta.get('tried_guidance_count')} |",
        f"| assigned_task_count | {meta.get('assigned_task_count')} |",
        f"| use_events | {meta.get('use_events')} |",
        f"| new_best_from_guidance | {meta.get('new_best_from_guidance')} |",
    ]
    guidance = list(meta.get("guidance") or [])
    lines.extend(
        ["", "| Guidance | Uses | Status | Outcome | Anchor Parent |", "|---|---:|---|---|---|"]
    )
    if not guidance:
        lines.append("| - | - | - | - | - |")
        return lines
    for item in guidance:
        uses = f"{item.get('uses')}/{item.get('max_uses')}"
        lines.append(
            f"| {item.get('guidance_id') or ''}: {item.get('idea') or ''} | {uses} | "
            f"{item.get('status') or ''} | {item.get('outcome') or ''} | "
            f"{item.get('anchor_parent_id') or ''} |"
        )
    return lines


def _attempts_table(rows: List[Dict[str, Any]]) -> List[str]:
    lines = [
        "| Task | Status | Entered SuccessPool | Fitness | Artifact | Verifier Result |",
        "|---|---|---|---:|---|---|",
    ]
    if not rows:
        lines.append("| - | - | - | - | - | - |")
        return lines
    for row in rows:
        lines.append(
            f"| {row.get('task_id') or ''} | {row.get('status') or ''} | "
            f"{row.get('in_success_pool')} | {_format_optional_float(row.get('fitness'))} | "
            f"`{row.get('artifact_ref') or ''}` | `{row.get('verification_result_ref') or ''}` |"
        )
    return lines


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return _format_float(value)
    if isinstance(value, (dict, list)):
        return "`" + json.dumps(value, ensure_ascii=False, sort_keys=True) + "`"
    return str(value)


def _format_float(value: float) -> str:
    if value is None or not math.isfinite(float(value)):
        return ""
    return f"{float(value):.6g}"


def _format_optional_float(value: Any) -> str:
    if value is None:
        return ""
    try:
        return _format_float(float(value))
    except (TypeError, ValueError):
        return str(value)


def _format_percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return ""


def _best_prefix(record_id: str, best_id: Optional[str]) -> str:
    return "\U0001f3c6 " if record_id == best_id else ""


def _node_id(record_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", record_id)
    if safe and safe[0].isdigit():
        safe = f"n_{safe}"
    return safe or "node"


def _quote_mermaid(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def _escape_mermaid_label(value: str) -> str:
    return value.replace('"', '\\"').replace("|", "/")


def _as_ref(path: Path) -> str:
    return path.as_posix()

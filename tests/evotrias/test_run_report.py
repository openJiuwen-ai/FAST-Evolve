"""Tests for evotrias.evolve_ctrl.run_report."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evotrias.db.attempts import AttemptRecord, Attempts
from evotrias.db.run_state import RunState
from evotrias.enums import SearchState
from evotrias.evolve_ctrl.lineage_tree import LineageTree
from evotrias.evolve_ctrl.run_report import write_run_report
from evotrias.evolve_ctrl.success_pool import SuccessPool

RUN_ID = "report_test"

SECTIONS = [
    "## Overview",
    "## Run Control Diagnostics",
    "## Lineage Graph",
    "## Top Results",
    "## Fitness Progression",
    "## Lineage Statistics",
    "## Meta-Evolve",
    "## Attempts",
    "## Deterministic Observations",
]


@pytest.fixture(autouse=True)
def cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _attempt(task_id: str, attempt_id: str, *, fitness=1.0, in_pool=True, **overrides) -> AttemptRecord:
    status = "success" if in_pool else "failed"
    fields = dict(
        id=attempt_id,
        run_id=RUN_ID,
        task_id=task_id,
        subagent_id="agent-1",
        artifact_ref=f"runs/{RUN_ID}/tasks/{task_id}/attempts/{attempt_id}/solution.py",
        parent_id=None,
        inspiration_ids=[],
        generation=0,
        verification_result={"status": status, "fitness": fitness if in_pool else None},
        in_success_pool=in_pool,
        created_at=f"2026-01-01T00:00:{int(attempt_id[-2:]):02d}+00:00",
    )
    fields.update(overrides)
    return AttemptRecord(**fields)


def _seed_and_write():
    log = Attempts(RUN_ID)
    log.append(_attempt("task_001", "attempt_01", fitness=0.4))
    log.append(_attempt("task_002", "attempt_02", fitness=0.7, parent_id="task_001", generation=1))
    log.append(_attempt("task_003", "attempt_03", in_pool=False))
    with RunState(RUN_ID) as state:
        state.search_state = SearchState.STOPPED
        state.stop_reason = "budget"
    return write_run_report(RUN_ID, SuccessPool(RUN_ID), LineageTree(), RunState(RUN_ID))


def test_refs_point_under_public_run_dir():
    refs = _seed_and_write()
    assert refs == {
        "report_ref": f"runs/{RUN_ID}/run_summary.md",
        "summary_ref": f"runs/{RUN_ID}/run_summary.json",
    }
    assert Path(refs["report_ref"]).is_file()
    assert Path(refs["summary_ref"]).is_file()


def test_json_summary_counts_and_attempt_fields():
    refs = _seed_and_write()
    summary = json.loads(Path(refs["summary_ref"]).read_text(encoding="utf-8"))

    assert summary["status"] == "stopped"
    assert summary["stop_reason"] == "budget"
    overview = summary["overview"]
    assert (overview["total_attempts"], overview["successful_records"], overview["failed_attempts"]) == (3, 2, 1)
    assert overview["best_task_id"] == "task_002"

    by_task = {row["task_id"]: row for row in summary["attempts"]}
    assert set(by_task) == {"task_001", "task_002", "task_003"}
    assert by_task["task_001"] == {
        "id": "attempt_01",
        "task_id": "task_001",
        "status": "success",
        "fitness": 0.4,
        "in_success_pool": True,
        "success_record_id": None,
        "parent_id": None,
        "meta_guidance_id": None,
        "anchor_parent_id": None,
        "generation": 0,
        "artifact_ref": f"runs/{RUN_ID}/tasks/task_001/attempts/attempt_01/solution.py",
        "verification_result_ref": None,
        "created_at": "2026-01-01T00:00:01+00:00",
    }
    assert by_task["task_002"]["parent_id"] == "task_001"
    assert by_task["task_002"]["generation"] == 1
    assert by_task["task_003"]["status"] == "failed"
    assert by_task["task_003"]["in_success_pool"] is False


def test_markdown_has_every_section():
    refs = _seed_and_write()
    text = Path(refs["report_ref"]).read_text(encoding="utf-8")
    assert text.startswith(f"# Evolution Run Summary: {RUN_ID}")
    for section in SECTIONS:
        assert section in text, section
    assert "task_002" in text


def test_rerun_is_idempotent():
    first = _seed_and_write()
    before = json.loads(Path(first["summary_ref"]).read_text(encoding="utf-8"))
    second = write_run_report(RUN_ID, SuccessPool(RUN_ID), LineageTree(), RunState(RUN_ID))
    after = json.loads(Path(second["summary_ref"]).read_text(encoding="utf-8"))
    before.pop("generated_at")
    after.pop("generated_at")
    assert before == after

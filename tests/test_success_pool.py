"""Tests for evotrias.mcp.success_pool."""

from __future__ import annotations

import pytest

from evotrias.db.attempts import AttemptRecord, Attempts
from evotrias.db.task_contexts import TaskContexts
from evotrias.mcp.success_pool import SuccessPool, SuccessRecord

RUN_ID = "pool_test"


@pytest.fixture(autouse=True)
def cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _attempt(task_id: str, attempt_id: str, *, fitness=1.0, in_pool=True, **overrides) -> AttemptRecord:
    fields = dict(
        id=attempt_id,
        run_id=RUN_ID,
        task_id=task_id,
        subagent_id="agent-1",
        artifact_ref=f"runs/{RUN_ID}/tasks/{task_id}/attempts/{attempt_id}/solution.py",
        parent_id=None,
        inspiration_ids=[],
        generation=0,
        verification_result={"status": "success", "fitness": fitness},
        in_success_pool=in_pool,
        created_at=f"2026-01-01T00:00:{int(attempt_id[-2:]):02d}+00:00",
    )
    fields.update(overrides)
    return AttemptRecord(**fields)


def test_empty_run_yields_empty_pool():
    pool = SuccessPool(RUN_ID)
    assert pool.is_empty()
    assert pool.get_all() == []
    assert pool.get("task_001") is None
    assert pool.get_best_record() is None
    assert pool.infer_next_generation("task_001") == 0
    assert "task_001" not in pool


def test_pool_is_derived_from_flagged_attempts_only():
    log = Attempts(RUN_ID)
    log.append(_attempt("task_001", "attempt_01", fitness=0.5))
    log.append(_attempt("task_002", "attempt_02", fitness=0.9, in_pool=False))
    log.append(_attempt("task_003", "attempt_03", fitness=0.7))
    pool = SuccessPool(RUN_ID)
    assert [r.id for r in pool.get_all()] == ["task_001", "task_003"]
    assert "task_002" not in pool
    assert pool.get_best_record().id == "task_003"


def test_from_attempt_maps_fields():
    attempt = _attempt(
        "task_007",
        "attempt_02",
        fitness=0.42,
        parent_id="task_001",
        inspiration_ids=["task_002", "task_003"],
        generation=3,
        verification_result={
            "status": "success",
            "fitness": 0.42,
            "summary": "tighter packing",
            "metrics": {"score": 0.42, "n": 26},
            "profile": {"runtime": 1.5},
            "evaluator_version": "v2",
            "log_ref": "log.txt",
        },
        verification_result_ref="eval.json",
    )
    record = SuccessRecord.from_attempt(attempt, selection_count=4)
    assert record.id == record.task_id == "task_007"
    assert (record.fitness, record.parent_id, record.generation, record.selection_count) == (0.42, "task_001", 3, 4)
    assert record.sketch == "tighter packing"
    assert record.metrics == {"score": 0.42, "n": 26}
    assert record.profile == {"runtime": 1.5}
    assert record.verification_result_ref == "eval.json"
    assert record.created_at == attempt.created_at
    assert record.metadata["attempt_id"] == "attempt_02"
    assert record.metadata["inspiration_ids"] == ["task_002", "task_003"]
    assert record.metadata["evaluator_version"] == "v2"
    assert record.metadata["log_ref"] == "log.txt"


def test_profile_falls_back_to_metrics_when_absent():
    attempt = _attempt("task_001", "attempt_01", verification_result={"status": "success", "fitness": 1.0, "metrics": {"m": 1}})
    record = SuccessRecord.from_attempt(attempt)
    assert record.profile == {"m": 1}


def test_selection_count_is_derived_from_task_contexts():
    Attempts(RUN_ID).append(_attempt("task_001", "attempt_01"))
    Attempts(RUN_ID).append(_attempt("task_002", "attempt_02", parent_id="task_001", generation=1))
    with TaskContexts(RUN_ID) as contexts:
        for child in ("task_002", "task_003", "task_004"):
            ctx = contexts.get(child, create=True)
            ctx.parent_id = "task_001"
            ctx.parent_selected = True
        ctx = contexts.get("task_005", create=True)
        ctx.parent_id = "task_001"
        ctx.parent_selected = False  # selection not finalised: does not count
    pool = SuccessPool(RUN_ID)
    assert pool.get("task_001").selection_count == 3
    assert pool.get("task_002").selection_count == 0
    assert pool.infer_next_generation("task_002") == 2

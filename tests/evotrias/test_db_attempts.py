# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Tests for evotrias.db.attempts."""

from __future__ import annotations

import json

import pytest

from evotrias.db import paths
from evotrias.db.attempts import AttemptRecord, Attempts

RUN_ID = "attempts_test"


@pytest.fixture(autouse=True)
def cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _attempt(task_id: str, attempt_id: str, **overrides) -> AttemptRecord:
    fields = dict(
        id=attempt_id,
        run_id=RUN_ID,
        task_id=task_id,
        subagent_id="agent",
        artifact_ref=f"runs/{RUN_ID}/tasks/{task_id}/attempts/{attempt_id}/solution.py",
        parent_id=None,
        inspiration_ids=[],
        generation=0,
        verification_result={"status": "success", "fitness": 1.0},
    )
    fields.update(overrides)
    return AttemptRecord(**fields)


def test_missing_file_is_empty():
    log = Attempts(RUN_ID)
    assert list(log) == []
    assert len(log) == 0
    assert log.find_by_task_id("task_001") is None


def test_append_then_iterate_yields_records_in_order():
    log = Attempts(RUN_ID)
    log.append(_attempt("task_001", "attempt_001"))
    log.append(_attempt("task_001", "attempt_002", in_success_pool=True))
    got = list(log)
    assert [a.id for a in got] == ["attempt_001", "attempt_002"]
    assert all(isinstance(a, AttemptRecord) for a in got)
    assert got[1].in_success_pool is True
    assert got[1].verification_result == {"status": "success", "fitness": 1.0}
    assert (paths.run_dir(RUN_ID) / paths.ATTEMPTS).exists()


def test_find_helpers():
    log = Attempts(RUN_ID)
    log.append(_attempt("task_001", "attempt_001"))
    log.append(_attempt("task_002", "attempt_001"))
    log.append(_attempt("task_001", "attempt_002"))
    assert log.find_by_task_id("task_001").id == "attempt_002"
    assert log.find_by_task_id("task_002").id == "attempt_001"
    assert log.find_by_task_attempt("task_001", "attempt_001").task_id == "task_001"
    assert log.find_by_task_attempt("task_001", "attempt_009") is None


def test_unknown_keys_on_disk_are_ignored():
    path = paths.run_dir(RUN_ID) / paths.ATTEMPTS
    path.parent.mkdir(parents=True)
    row = {
        "id": "attempt_001",
        "run_id": RUN_ID,
        "task_id": "task_001",
        "subagent_id": "agent",
        "artifact_ref": "x.py",
        "parent_id": None,
        "inspiration_ids": [],
        "generation": 0,
        "verification_result": {},
        "retired_field": "gone",
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    [got] = list(Attempts(RUN_ID))
    assert got.id == "attempt_001"
    assert not hasattr(got, "retired_field")


def test_successes_maps_task_id_to_its_success_attempt():
    log = Attempts(RUN_ID)
    assert log.successes == {}
    log.append(_attempt("task_001", "attempt_001", verification_result={"status": "failure"}))
    log.append(_attempt("task_001", "attempt_002", in_success_pool=True))
    log.append(_attempt("task_002", "attempt_001"))  # finished, never entered the success pool
    successes = log.successes
    assert set(successes) == {"task_001"}
    assert successes["task_001"].id == "attempt_002"


def test_final_by_task_prefers_pool_entry_else_latest():
    log = Attempts(RUN_ID)
    assert log.final_by_task == {}
    log.append(_attempt("task_001", "attempt_001", in_success_pool=True))
    log.append(_attempt("task_002", "attempt_001", verification_result={"status": "failure"}))
    log.append(_attempt("task_002", "attempt_002", verification_result={"status": "failure"}))
    log.append(_attempt("task_003", "attempt_001", verification_result={"status": "failure"}))
    log.append(_attempt("task_003", "attempt_002", in_success_pool=True))
    log.append(_attempt("task_003", "attempt_003", verification_result={"status": "failure"}))
    final = log.final_by_task
    assert list(final) == ["task_001", "task_002", "task_003"]
    assert final["task_001"].id == "attempt_001"
    assert final["task_002"].id == "attempt_002" and not final["task_002"].in_success_pool
    assert final["task_003"].id == "attempt_002" and final["task_003"].in_success_pool

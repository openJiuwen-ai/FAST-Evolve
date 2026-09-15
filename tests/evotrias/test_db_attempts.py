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
    log.append(_attempt("task_001", "attempt_002", entered_success_db=True))
    got = list(log)
    assert [a.id for a in got] == ["attempt_001", "attempt_002"]
    assert all(isinstance(a, AttemptRecord) for a in got)
    assert got[1].entered_success_db is True
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

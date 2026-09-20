"""Tests for evotrias.db.run_state."""

from __future__ import annotations

import json

import pytest

from evotrias.db import paths
from evotrias.db.run_state import RunState
from evotrias.enums import SearchState

RUN_ID = "run_state_test"


@pytest.fixture(autouse=True)
def cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_missing_file_yields_defaults_and_no_elapsed():
    state = RunState(RUN_ID)
    assert state.task_description == ""
    assert state.search_state is SearchState.EXPLORING
    assert state.stop_reason == ""
    assert state.diagnostics == {}
    assert state.last_checked_attempt_count == 0
    assert state.created_at == ""
    assert state.elapsed_seconds() is None


def test_first_write_starts_the_run_clock():
    with RunState(RUN_ID) as state:
        state.task_description = "pack circles"
    reloaded = RunState(RUN_ID)
    assert reloaded.task_description == "pack circles"
    assert reloaded.created_at and reloaded.updated_at
    assert reloaded.elapsed_seconds() >= 0


def test_rewrite_keeps_created_at_and_moves_updated_at(monkeypatch):
    import evotrias.db.base as base

    clock = iter(["2026-01-01T00:00:00+00:00", "2026-01-01T00:05:00+00:00"])
    monkeypatch.setattr(base, "utc_now_iso", lambda: next(clock))
    with RunState(RUN_ID) as state:
        state.task_description = "x"
    with RunState(RUN_ID) as state:
        state.search_state = SearchState.STOPPED
        state.stop_reason = "manual"
    reloaded = RunState(RUN_ID)
    assert reloaded.created_at == "2026-01-01T00:00:00+00:00"
    assert reloaded.updated_at == "2026-01-01T00:05:00+00:00"
    assert (reloaded.search_state, reloaded.stop_reason, reloaded.task_description) == (SearchState.STOPPED, "manual", "x")


def test_on_disk_shape_has_no_retired_keys():
    with RunState(RUN_ID):
        pass
    on_disk = json.loads((paths.run_dir(RUN_ID) / paths.RUN_STATE).read_text(encoding="utf-8"))
    assert set(on_disk) == {
        "task_description",
        "search_state",
        "stop_reason",
        "diagnostics",
        "last_checked_attempt_count",
        "created_at",
        "updated_at",
    }

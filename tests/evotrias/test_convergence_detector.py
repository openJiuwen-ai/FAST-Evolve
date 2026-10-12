"""Tests for evotrias.evolve_ctrl.convergence_detector."""

from __future__ import annotations

import pytest

from evotrias.db.attempts import AttemptRecord, Attempts
from evotrias.db.config import PlateauConfig
from evotrias.db.run_state import RunState
from evotrias.enums import SearchState
from evotrias.evolve_ctrl.convergence_detector import FitnessPlateauDetector
from evotrias.evolve_ctrl.lineage_tree import LineageTree
from evotrias.evolve_ctrl.success_pool import SuccessPool

RUN_ID = "plateau_test"


@pytest.fixture(autouse=True)
def cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _seed_successes(fitnesses):
    log = Attempts(RUN_ID)
    for index, fitness in enumerate(fitnesses, start=1):
        log.append(
            AttemptRecord(
                id=f"attempt_{index:02d}",
                run_id=RUN_ID,
                task_id=f"task_{index:03d}",
                subagent_id="agent-1",
                artifact_ref=f"runs/{RUN_ID}/tasks/task_{index:03d}/attempts/attempt_01/solution.py",
                parent_id=None,
                inspiration_ids=[],
                generation=0,
                verification_result={"status": "success", "fitness": fitness},
                in_success_pool=True,
                created_at=f"2026-01-01T00:00:{index:02d}+00:00",
            )
        )
    pool = SuccessPool(RUN_ID)
    tree = LineageTree()
    tree.rebuild(pool)
    return pool, tree


def _detector(window=2, patience=2, threshold=0.0):
    return FitnessPlateauDetector(PlateauConfig(window=window, improvement_threshold=threshold, patience=patience))


def test_too_few_successes_stays_exploring():
    pool, tree = _seed_successes([1.0, 1.0, 1.0, 1.0])
    state, reason, diagnostics = _detector().evaluate(RunState(RUN_ID), pool, tree)
    assert state is SearchState.EXPLORING
    assert reason == ""
    assert diagnostics["total_success_tracked"] == 4


def test_plateau_beyond_patience_enters_watching():
    pool, tree = _seed_successes([1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    state, reason, diagnostics = _detector(window=2, patience=2).evaluate(RunState(RUN_ID), pool, tree)
    assert state is SearchState.WATCHING
    assert reason == ""
    assert diagnostics["plateau_detected"] is True
    assert diagnostics["plateau_count"] >= 2


def test_new_best_resets_to_exploring():
    pool, tree = _seed_successes([1.0, 1.0, 1.0, 1.0, 1.0, 2.0])
    state, _, diagnostics = _detector(window=2, patience=2).evaluate(RunState(RUN_ID), pool, tree)
    assert state is SearchState.EXPLORING
    assert diagnostics["latest_success_is_new_best"] is True


def test_stop_reason_forces_stopped():
    pool, tree = _seed_successes([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    state, reason, diagnostics = _detector().evaluate(RunState(RUN_ID), pool, tree, stop_reason="budget")
    assert state is SearchState.STOPPED
    assert reason == "budget"
    assert diagnostics["state"] == "stopped" and diagnostics["stop_reason"] == "budget"


def test_stopped_is_sticky():
    pool, tree = _seed_successes([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    previous = RunState(RUN_ID)
    previous.search_state = SearchState.STOPPED
    previous.stop_reason = "manual_finalize"
    state, reason, _ = _detector().evaluate(previous, pool, tree)
    assert state is SearchState.STOPPED
    assert reason == "manual_finalize"


def test_diagnostics_shape():
    pool, tree = _seed_successes([1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    state, reason, diagnostics = _detector().evaluate(RunState(RUN_ID), pool, tree)
    assert list(diagnostics) == [
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
    assert diagnostics["state"] == state.value
    assert diagnostics["stop_reason"] == reason

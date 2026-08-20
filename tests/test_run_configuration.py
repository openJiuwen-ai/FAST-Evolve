"""Tests for decoupled run-shape configuration.

``lineage_root_number`` (root-phase mint cap: at most this many tasks exist
until all of them finish) and ``max_concurrent_tasks`` (in-flight target,
enforced in every phase) are independent knobs recorded by ``configure_run``
and consumed by ``check_convergence``.
"""

from __future__ import annotations

import pytest

from agent_evolve_mcp import public_tools as public_tools_module
from agent_evolve_mcp.controller import EvolutionController
from agent_evolve_mcp.state_store import RunStateStore
from evotrias import scheduler as sched

RUN_ID = "run_shape_test"


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "AGENT_EVOLVE_CONTROLLER_DIR", str(tmp_path / ".agent_evolve" / "controller")
    )
    monkeypatch.setenv("AGENT_EVOLVE_UI_ENABLED", "0")
    monkeypatch.setenv("AGENT_EVOLVE_NOTES_ENABLED", "0")
    monkeypatch.setattr(sched, "_STATE_DIR", tmp_path / ".evotrias" / "state")
    public_tools_module._controllers.pop(RUN_ID, None)
    yield
    public_tools_module._controllers.pop(RUN_ID, None)


def _finish_one_task(run_id: str, agent_id: str, *, fitness=1.0) -> str:
    """Walk one pool task through allocate -> queue -> start -> complete."""
    task_id = sched.allocate_task(run_id)
    sched.queue_task(run_id, task_id, agent_id)
    sched.start_task(run_id, agent_id)
    sched.complete_task(run_id, task_id, fitness)
    return task_id


def test_configure_run_records_and_returns_run_shape(run_env):
    result = public_tools_module.evolve_configure_run(
        RUN_ID,
        max_total_tasks=30,
        lineage_root_number=6,
        max_concurrent_tasks=2,
        task_description="decouple the knobs",
    )

    assert result["configured"] is True
    assert result["max_total_tasks"] == 30
    assert result["lineage_root_number"] == 6
    assert result["max_concurrent_tasks"] == 2
    assert RunStateStore(RUN_ID).load().run_config == {
        "max_total_tasks": 30,
        "lineage_root_number": 6,
        "max_concurrent_tasks": 2,
    }


def test_configure_run_is_locked_in_process(run_env):
    public_tools_module.evolve_configure_run(
        RUN_ID, max_total_tasks=30, lineage_root_number=6, max_concurrent_tasks=2
    )
    second = public_tools_module.evolve_configure_run(
        RUN_ID, max_total_tasks=99, lineage_root_number=1, max_concurrent_tasks=9
    )

    assert second["configured"] is False
    assert second["max_total_tasks"] == 30
    assert second["lineage_root_number"] == 6
    assert second["max_concurrent_tasks"] == 2


def test_configure_run_is_locked_across_process_restarts(run_env):
    public_tools_module.evolve_configure_run(
        RUN_ID, max_total_tasks=30, lineage_root_number=6, max_concurrent_tasks=2
    )
    # A new MCP process has an empty controller cache but the same disk state.
    public_tools_module._controllers.pop(RUN_ID, None)

    second = public_tools_module.evolve_configure_run(
        RUN_ID, max_total_tasks=99, lineage_root_number=1, max_concurrent_tasks=9
    )

    assert second["configured"] is False
    assert second["max_total_tasks"] == 30
    assert second["lineage_root_number"] == 6
    assert second["max_concurrent_tasks"] == 2


def test_invalid_run_shape_values_are_clamped(run_env):
    result = public_tools_module.evolve_configure_run(
        RUN_ID, lineage_root_number=0, max_concurrent_tasks=-2
    )

    assert result["lineage_root_number"] == 1
    assert result["max_concurrent_tasks"] == 1


def test_root_phase_caps_minting_and_barrier_gates_evolution(run_env):
    # max_concurrent_tasks=2, lineage_root_number=3: create 2, then 1, then
    # wait for all 3 roots before evolution tasks flow at concurrency 2.
    public_tools_module.evolve_configure_run(
        RUN_ID, max_total_tasks=30, lineage_root_number=3, max_concurrent_tasks=2
    )

    def check():
        result = public_tools_module.evolve_check_convergence(RUN_ID, concurrency=2)
        assert result["action"] == "continue"
        return result["tasks_to_create"]

    assert check() == 2  # root fill respects the concurrency cap, not 3
    _finish_one_task(RUN_ID, "agent_0")
    assert check() == 1  # top back to 2 in flight, but mint no 4th root
    assert check() == 0  # barrier: all 3 roots minted, 2 still unfinished
    _finish_one_task(RUN_ID, "agent_1")
    assert check() == 0  # barrier: 1 root still unfinished
    _finish_one_task(RUN_ID, "agent_2")
    assert check() == 2  # every root finished: evolution fill to concurrency
    _finish_one_task(RUN_ID, "agent_3")
    assert check() == 1  # steady state: top up to 2 after each completion
    # check_convergence rewrites run state; the recorded shape must survive it.
    assert RunStateStore(RUN_ID).load().run_config == {
        "max_total_tasks": 30,
        "lineage_root_number": 3,
        "max_concurrent_tasks": 2,
    }


def test_small_root_budget_finishes_before_wider_concurrency(run_env):
    # max_concurrent_tasks=3, lineage_root_number=2: create 2, wait for both,
    # then evolution tasks flow at concurrency 3.
    public_tools_module.evolve_configure_run(
        RUN_ID, max_total_tasks=30, lineage_root_number=2, max_concurrent_tasks=3
    )

    def check():
        result = public_tools_module.evolve_check_convergence(RUN_ID, concurrency=3)
        assert result["action"] == "continue"
        return result["tasks_to_create"]

    assert check() == 2  # root budget caps the fill below concurrency 3
    _finish_one_task(RUN_ID, "agent_0")
    assert check() == 0  # barrier: the other root is still unfinished
    _finish_one_task(RUN_ID, "agent_1")
    assert check() == 3  # both roots finished: evolution fill to concurrency
    _finish_one_task(RUN_ID, "agent_2")
    assert check() == 1  # steady state: top up to 3 after each completion


def test_check_convergence_rejects_non_positive_concurrency(run_env):
    public_tools_module.evolve_configure_run(
        RUN_ID, max_total_tasks=30, lineage_root_number=1, max_concurrent_tasks=3
    )

    with pytest.raises(ValueError, match="concurrency must be a positive integer"):
        public_tools_module.evolve_check_convergence(RUN_ID, concurrency=0)
    with pytest.raises(ValueError, match="concurrency must be a positive integer"):
        public_tools_module.evolve_check_convergence(RUN_ID, concurrency=-2)


def test_restarted_controller_recovers_recorded_config(run_env):
    public_tools_module.evolve_configure_run(
        RUN_ID, max_total_tasks=30, lineage_root_number=6, max_concurrent_tasks=2
    )

    restarted = EvolutionController(RUN_ID)

    assert restarted.config.budget.max_total_tasks == 30
    assert restarted.config.scheduler.lineage_root_number == 6
    assert restarted.config.scheduler.max_concurrent_tasks == 2

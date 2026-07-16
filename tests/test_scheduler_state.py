import json

import pytest

from agent_evolve_mcp.scheduler_state import (
    get_scheduler_state,
    initialize_scheduler_state,
    update_task_status,
)


def test_scheduler_state_tracks_transitions_and_returns_compact_active_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    initial = initialize_scheduler_state("run_test")
    assert initial == {
        "run_id": "run_test",
        "running_tasks": [],
        "active_agents": {},
        "running_count": 0,
    }

    update_task_status("run_test", "task_001", "running", "agent-one")
    update_task_status("run_test", "task_002", "running", "agent-two")
    completed = update_task_status("run_test", "task_001", "completed")

    assert completed == {
        "updated": True,
        "task_id": "task_001",
        "status": "completed",
        "running_count": 1,
    }
    assert get_scheduler_state("run_test") == {
        "run_id": "run_test",
        "running_tasks": ["task_002"],
        "active_agents": {"task_002": "agent-two"},
        "running_count": 1,
    }

    persisted = json.loads(
        (tmp_path / "runs" / "run_test" / "scheduler_state.json").read_text(encoding="utf-8")
    )
    assert persisted["task_agents"]["task_001"] == {
        "agent_id": "agent-one",
        "status": "completed",
    }


def test_scheduler_initialization_does_not_erase_existing_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    update_task_status("run_test", "task_001", "running", "agent-one")

    initialize_scheduler_state("run_test")

    assert get_scheduler_state("run_test")["running_tasks"] == ["task_001"]


def test_scheduler_state_rejects_invalid_transition(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="running.*completed"):
        update_task_status("run_test", "task_001", "paused")

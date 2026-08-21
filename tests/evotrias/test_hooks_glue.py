"""Tests for evotrias.hooks glue (scheduler)."""

from __future__ import annotations

import json
import os

import pytest

from evotrias import scheduler as sched
from evotrias.enums import Agents
from evotrias.hooks import broker
from evotrias.hooks import hook_manager
from evotrias.hooks.claude import scheduler_hooks as sched_glue


@pytest.fixture
def tmp_env(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    os.makedirs(repo, exist_ok=True)
    # _STATE_DIR is a cwd-relative constant; pin it to an absolute path under
    # the temp repo so the path-building helpers stay untouched and no state
    # leaks into the real .evotrias/.
    monkeypatch.setattr(sched, "_STATE_DIR", repo / ".evotrias" / "state")
    # runs/ workspace also lives under the repo root for completed_tasks.jsonl
    monkeypatch.chdir(repo)
    # Reset handler registry and re-import the glue modules so their
    # @register side effects repopulate the broker against a clean slate.
    broker._handlers.clear()
    import importlib
    importlib.reload(sched_glue)
    return repo


def _spawn_tool_input(prompt="task_description: number guessing"):
    """Tool input of an orchestrator Agent call spawning a task-executor."""
    return {
        "description": "Execute replenishment task",
        "prompt": prompt,
        "subagent_type": "agent_evolve_plugin:task-executor",
        "run_in_background": True,
    }


def _start_executor(run_id, agent_id):
    """Walk one task through the full chain: allocate, bind, subagent_start."""
    task_id = sched.allocate_task(run_id)
    sched.queue_task(run_id, task_id, agent_id)
    hook_manager.dispatch(
        "claude",
        "subagent_start",
        {
            "session_id": "sess-1",
            "agent_id": agent_id,
            "agent_type": "agent_evolve_plugin:task-executor",
            "hook_event_name": "SubagentStart",
        },
    )
    return task_id


def test_executor_spawn_full_chain(tmp_env):
    sched.remember_session_run("sess-1", "run-Z")
    sched.top_up_tasks("run-Z", target=2, max_tasks=100)

    # 1. PreToolUse on the orchestrator's Agent call: allocate a task and
    #    rewrite the tool input so the executor learns its identity.
    out = hook_manager.dispatch(
        "claude",
        "pre_tool_use",
        {
            "session_id": "sess-1",
            "hook_event_name": "PreToolUse",
            "tool_name": "Agent",
            "tool_input": _spawn_tool_input(),
        },
    )
    assert out is not None
    updated = out["hookSpecificOutput"]["updatedInput"]
    assert updated["description"] == "task_001"
    assert updated["prompt"].endswith("\nrun_id:run-Z\ntask_id:task_001\n")
    assert updated["subagent_type"] == "agent_evolve_plugin:task-executor"
    assert updated["run_in_background"] is True
    snap = sched.pool_snapshot("run-Z")
    assert snap["created"] == ["task_002"]
    assert snap["pending"] == ["task_001"]

    # 2. PostToolUse: bind the spawned agentId to the task. No indexing yet.
    out = hook_manager.dispatch(
        "claude",
        "post_tool_use",
        {
            "session_id": "sess-1",
            "hook_event_name": "PostToolUse",
            "tool_name": "Agent",
            "tool_input": updated,
            "tool_response": {"isAsync": True, "agentId": "agent-5", "description": "task_001"},
        },
    )
    assert out is None
    snap = sched.pool_snapshot("run-Z")
    assert snap["pending"] == []
    assert snap["queued"] == {"task_001": "agent-5"}
    assert sched.lookup_agent("run-Z", "agent-5") is None, "agent_index is subagent_start's job"

    # 3. SubagentStart: index the agent with its task and move it to running.
    out = hook_manager.dispatch(
        "claude",
        "subagent_start",
        {
            "session_id": "sess-1",
            "agent_id": "agent-5",
            "agent_type": "agent_evolve_plugin:task-executor",
            "hook_event_name": "SubagentStart",
        },
    )
    assert out is None
    assert sched.lookup_agent("run-Z", "agent-5") == {
        "agent_type": Agents.TASK_EXECUTOR,
        "task_id": "task_001",
    }
    snap = sched.pool_snapshot("run-Z")
    assert snap["queued"] == {}
    assert snap["running"] == {"task_001": "agent-5"}


def test_executor_bind_backfills_index_when_subagent_start_ran_first(tmp_env):
    # SubagentStart and the spawner's PostToolUse are parallel processes; when
    # the subagent starts first, remember_agent indexes task_id=None and
    # executor_bind_agent must backfill it afterwards.
    sched.remember_session_run("sess-1", "run-race")
    sched.top_up_tasks("run-race", target=1, max_tasks=100)
    sched.allocate_task("run-race")

    out = hook_manager.dispatch(
        "claude",
        "subagent_start",
        {
            "session_id": "sess-1",
            "agent_id": "agent-5",
            "agent_type": "agent_evolve_plugin:task-executor",
            "hook_event_name": "SubagentStart",
        },
    )
    assert out is None
    assert sched.lookup_agent("run-race", "agent-5") == {
        "agent_type": Agents.TASK_EXECUTOR,
        "task_id": None,
    }

    hook_manager.dispatch(
        "claude",
        "post_tool_use",
        {
            "session_id": "sess-1",
            "hook_event_name": "PostToolUse",
            "tool_name": "Agent",
            "tool_input": _spawn_tool_input(),
            "tool_response": {"isAsync": True, "agentId": "agent-5", "description": "task_001"},
        },
    )
    assert sched.lookup_agent("run-race", "agent-5") == {
        "agent_type": Agents.TASK_EXECUTOR,
        "task_id": "task_001",
    }
    snap = sched.pool_snapshot("run-race")
    assert snap["queued"] == {}
    assert snap["running"] == {"task_001": "agent-5"}


def test_executor_spawn_passes_through_on_empty_pool(tmp_env):
    # Empty created pool is CRITICAL (logged); the spawn passes through untouched.
    sched.remember_session_run("sess-1", "run-empty")
    out = hook_manager.dispatch(
        "claude",
        "pre_tool_use",
        {
            "session_id": "sess-1",
            "hook_event_name": "PreToolUse",
            "tool_name": "Agent",
            "tool_input": _spawn_tool_input(),
        },
    )
    assert out is None, "tool input must be left untouched when no task is available"


def test_executor_spawn_skips_when_no_run_for_session(tmp_env):
    # No session->run mapping: not an evolve run at all, so the Agent call
    # passes through untouched and nothing is allocated.
    out = hook_manager.dispatch(
        "claude",
        "pre_tool_use",
        {
            "session_id": "sess-unknown",
            "hook_event_name": "PreToolUse",
            "tool_name": "Agent",
            "tool_input": _spawn_tool_input(),
        },
    )
    assert out is None


def test_subagent_start_skips_when_no_run_for_session(tmp_env):
    # No session->run mapping: not an evolve run at all, so nothing is indexed,
    # regardless of agent type.
    payload = {
        "session_id": "sess-unknown",
        "agent_id": "agent-7",
        "agent_type": "agent_evolve_plugin:task-executor",
        "hook_event_name": "SubagentStart",
    }
    out = hook_manager.dispatch("claude", "subagent_start", payload)
    assert out is None
    assert sched.lookup_agent("run-any", "agent-7") is None


def test_subagent_start_indexes_non_executors_without_claiming_a_task(tmp_env):
    # hooks.json matches SubagentStart with "*", so every subagent in an evolve
    # session reaches this handler.  The task pool belongs to task-executors
    # alone, but the index covers everyone — including UNKNOWN types — so
    # lookup can resolve any agent it is asked about.
    sched.remember_session_run("sess-1", "run-mixed")
    sched.top_up_tasks("run-mixed", target=2, max_tasks=100)
    expected = {
        "agent_evolve_plugin:init-explorer": {"agent_type": Agents.INIT_EXPLORER},
        "agent_evolve_plugin:meta-guidance-generator": {"agent_type": Agents.META_GUIDANCE_GENERATOR},
        "general-purpose": {"agent_type": Agents.UNKNOWN, "origin_agent_type": "general-purpose"},
        "Explore": {"agent_type": Agents.UNKNOWN, "origin_agent_type": "Explore"},
    }
    for agent_type, want in expected.items():
        out = hook_manager.dispatch(
            "claude",
            "subagent_start",
            {
                "session_id": "sess-1",
                "agent_id": f"agent-{agent_type}",
                "agent_type": agent_type,
                "hook_event_name": "SubagentStart",
            },
        )
        assert out is None, f"{agent_type} must get no task injection"
        assert sched.lookup_agent("run-mixed", f"agent-{agent_type}") == want

    snap = sched.pool_snapshot("run-mixed")
    assert snap["created"] == ["task_001", "task_002"], "created pool must be untouched"
    assert snap["running"] == {}

    # A task-executor in the same session still lands its slot via the chain.
    task_id = _start_executor("run-mixed", "agent-exec")
    assert task_id == "task_001"
    assert sched.lookup_agent("run-mixed", "agent-exec") == {
        "agent_type": Agents.TASK_EXECUTOR,
        "task_id": "task_001",
    }

    # A run-scoped agent's normal sign-off must not land as a failure reason.
    hook_manager.dispatch(
        "claude",
        "subagent_stop",
        {
            "session_id": "sess-1",
            "agent_id": "agent-general-purpose",
            "agent_type": "general-purpose",
            "hook_event_name": "SubagentStop",
            "last_assistant_message": "Here is the summary you asked for.",
        },
    )
    assert sched.pool_snapshot("run-mixed")["failed"] == {}
    assert sched.pool_snapshot("run-mixed")["running"] == {"task_001": "agent-exec"}


def test_configure_run_post_records_session_run(tmp_env):
    payload = {
        "session_id": "sess-9",
        "tool_name": "mcp__plugin_agent_evolve_plugin_ctrl__configure_run",
        "tool_input": {"run_id": "run-9", "max_total_tasks": 50},
        "hook_event_name": "PostToolUse",
    }
    out = hook_manager.dispatch("claude", "post_tool_use", payload)
    assert out is None
    assert sched.lookup_run_by_session("sess-9") == "run-9"


def test_subagent_stop_completes_task_from_jsonl(tmp_env):
    sched.remember_session_run("sess-1", "run-W")
    sched.top_up_tasks("run-W", target=1, max_tasks=100)
    _start_executor("run-W", "agent-10")
    # Write a completion record for task_001
    os.makedirs("runs/run-W", exist_ok=True)
    with open("runs/run-W/completed_tasks.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps({"task_id": "task_001", "fitness": 0.91, "status": "success", "artifact_ref": "x"}) + "\n")
    payload = {
        "session_id": "sess-1",
        "agent_id": "agent-10",
        "agent_type": "agent_evolve_plugin:task-executor",
        "hook_event_name": "SubagentStop",
    }
    hook_manager.dispatch("claude", "subagent_stop", payload)
    snap = sched.pool_snapshot("run-W")
    assert snap["completed"] == {"task_001": {"fitness": 0.91}}
    assert snap["running"] == {}


def test_subagent_stop_fails_task_with_last_message(tmp_env):
    sched.remember_session_run("sess-1", "run-V")
    sched.top_up_tasks("run-V", target=1, max_tasks=100)
    _start_executor("run-V", "agent-11")
    payload = {
        "session_id": "sess-1",
        "agent_id": "agent-11",
        "agent_type": "agent_evolve_plugin:task-executor",
        "hook_event_name": "SubagentStop",
        "last_assistant_message": "I could not produce a solution: verifier timed out repeatedly." * 5,
    }
    hook_manager.dispatch("claude", "subagent_stop", payload)
    snap = sched.pool_snapshot("run-V")
    assert "task_001" in snap["failed"]
    reason = snap["failed"]["task_001"]["reason"]
    assert len(reason) <= 200
    assert "verifier timed out" in reason

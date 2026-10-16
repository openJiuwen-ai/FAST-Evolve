"""Tests for evotrias.hooks glue (permission + scheduler)."""

from __future__ import annotations

import os

import pytest

from evotrias import scheduler as sched
from evotrias.db.agent_index import AgentInfo
from evotrias.db.attempts import AttemptRecord, Attempts
from evotrias.db.session_map import SessionMap
from evotrias.enums_and_consts import Agents
from evotrias.hooks import broker, hook_manager
from evotrias.hooks.claude import permission_hooks as perm_glue
from evotrias.hooks.claude import scheduler_hooks as sched_glue


def _record_success(run_id: str, task_id: str, fitness: float) -> None:
    """Stand in for the controller's record_attempt: one successful attempt on disk."""
    Attempts(run_id).append(
        AttemptRecord(
            id="attempt_001",
            run_id=run_id,
            task_id=task_id,
            subagent_id="agent",
            artifact_ref=f"runs/{run_id}/tasks/{task_id}/attempts/attempt_001/solution.py",
            parent_id=None,
            inspiration_ids=[],
            generation=0,
            verification_result={"status": "success", "fitness": fitness},
            in_success_pool=True,
        )
    )


def _agents(bucket):
    """queued/running -> {task_id: agent_id}, dropping the timestamp."""
    return {tid: held.agent_id for tid, held in bucket.items()}


def _fitness(bucket):
    return {tid: done.fitness for tid, done in bucket.items()}


def _reasons(bucket):
    return {tid: failed.reason for tid, failed in bucket.items()}


@pytest.fixture
def tmp_env(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    os.makedirs(repo, exist_ok=True)
    # .evotrias is cwd-relative: chdir into the temp repo so no state leaks.
    monkeypatch.chdir(repo)
    # Reset handler registry and re-import the glue modules so their
    # @register side effects repopulate the broker against a clean slate.
    broker._handlers.clear()
    import importlib

    importlib.reload(perm_glue)
    importlib.reload(sched_glue)
    return repo


def _spawn_tool_input(prompt="task_description: number guessing"):
    """Tool input of an orchestrator Agent call spawning a task-executor."""
    return {
        "description": "Execute replenishment task",
        "prompt": prompt,
        "subagent_type": "evotrias-task-executor",
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
            "agent_type": "evotrias-task-executor",
            "hook_event_name": "SubagentStart",
        },
    )
    return task_id


def _make_task_executor_payload(agent_id, tool_name, tool_input, run_id, task_id):
    """Build a PreToolUse payload as if from a task-executor subagent."""
    return {
        "session_id": "sess-1",
        "agent_id": agent_id,
        "agent_type": "evotrias-task-executor",
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
    }


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
    assert updated["subagent_type"] == "evotrias-task-executor"
    assert updated["run_in_background"] is True
    snap = sched.pool_snapshot("run-Z")
    assert snap.created == ["task_002"]
    assert snap.pending == ["task_001"]

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
    assert snap.pending == []
    assert _agents(snap.queued) == {"task_001": "agent-5"}
    assert sched.lookup_agent("run-Z", "agent-5") is None, "agent_index is subagent_start's job"

    # 3. SubagentStart: index the agent with its task and move it to running.
    out = hook_manager.dispatch(
        "claude",
        "subagent_start",
        {
            "session_id": "sess-1",
            "agent_id": "agent-5",
            "agent_type": "evotrias-task-executor",
            "hook_event_name": "SubagentStart",
        },
    )
    assert out is None
    assert sched.lookup_agent("run-Z", "agent-5") == AgentInfo(Agents.TASK_EXECUTOR, {"task_id": "task_001"})
    snap = sched.pool_snapshot("run-Z")
    assert snap.queued == {}
    assert _agents(snap.running) == {"task_001": "agent-5"}


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
            "agent_type": "evotrias-task-executor",
            "hook_event_name": "SubagentStart",
        },
    )
    assert out is None
    assert sched.lookup_agent("run-race", "agent-5") == AgentInfo(Agents.TASK_EXECUTOR, {"task_id": None})

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
    assert sched.lookup_agent("run-race", "agent-5") == AgentInfo(Agents.TASK_EXECUTOR, {"task_id": "task_001"})
    snap = sched.pool_snapshot("run-race")
    assert snap.queued == {}
    assert _agents(snap.running) == {"task_001": "agent-5"}


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
    assert out is not None
    specific = out["hookSpecificOutput"]
    assert "updatedInput" not in specific, "tool input must be left untouched when no task is available"
    assert specific["permissionDecision"] == "allow"


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
        "agent_type": "evotrias-task-executor",
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
        "evotrias-init-explorer": AgentInfo(Agents.INIT_EXPLORER),
        "evotrias-meta-guidance-generator": AgentInfo(Agents.META_GUIDANCE_GENERATOR),
        "general-purpose": AgentInfo(Agents.UNKNOWN, {"origin_agent_type": "general-purpose"}),
        "Explore": AgentInfo(Agents.UNKNOWN, {"origin_agent_type": "Explore"}),
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
    assert snap.created == ["task_001", "task_002"], "created pool must be untouched"
    assert snap.running == {}

    # A task-executor in the same session still lands its slot via the chain.
    task_id = _start_executor("run-mixed", "agent-exec")
    assert task_id == "task_001"
    assert sched.lookup_agent("run-mixed", "agent-exec") == AgentInfo(Agents.TASK_EXECUTOR, {"task_id": "task_001"})

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
    assert sched.pool_snapshot("run-mixed").failed == {}
    assert _agents(sched.pool_snapshot("run-mixed").running) == {"task_001": "agent-exec"}


def test_configure_run_post_records_session_run(tmp_env):
    payload = {
        "session_id": "sess-9",
        "tool_name": "mcp__evotrias__configure_run",
        "tool_input": {"run_id": "run-9", "max_total_tasks": 50},
        "hook_event_name": "PostToolUse",
    }
    out = hook_manager.dispatch("claude", "post_tool_use", payload)
    assert out is None
    assert sched.lookup_run_by_session("sess-9") == "run-9"


def test_configure_run_post_without_session_id_registers_nothing(tmp_env):
    # A bare payload.get("session_id") used to hand None to the scheduler,
    # which str()-ed it into a literal "None" session key.
    payload = {
        "tool_name": "mcp__evotrias__configure_run",
        "tool_input": {"run_id": "run-9", "max_total_tasks": 50},
        "hook_event_name": "PostToolUse",
    }
    out = hook_manager.dispatch("claude", "post_tool_use", payload)
    assert out is None
    assert SessionMap().sessions == {}


def test_subagent_stop_completes_task_from_recorded_success(tmp_env):
    sched.remember_session_run("sess-1", "run-W")
    sched.top_up_tasks("run-W", target=1, max_tasks=100)
    _start_executor("run-W", "agent-10")
    _record_success("run-W", "task_001", 0.91)
    payload = {
        "session_id": "sess-1",
        "agent_id": "agent-10",
        "agent_type": "evotrias-task-executor",
        "hook_event_name": "SubagentStop",
    }
    hook_manager.dispatch("claude", "subagent_stop", payload)
    snap = sched.pool_snapshot("run-W")
    assert _fitness(snap.completed) == {"task_001": 0.91}
    assert snap.running == {}


def test_subagent_stop_fails_task_with_last_message(tmp_env):
    sched.remember_session_run("sess-1", "run-V")
    sched.top_up_tasks("run-V", target=1, max_tasks=100)
    _start_executor("run-V", "agent-11")
    payload = {
        "session_id": "sess-1",
        "agent_id": "agent-11",
        "agent_type": "evotrias-task-executor",
        "hook_event_name": "SubagentStop",
        "last_assistant_message": "I could not produce a solution: verifier timed out repeatedly." * 5,
    }
    hook_manager.dispatch("claude", "subagent_stop", payload)
    snap = sched.pool_snapshot("run-V")
    assert "task_001" in snap.failed
    reason = snap.failed["task_001"].reason
    assert len(reason) <= 200
    assert "verifier timed out" in reason


def test_pre_tool_use_allows_task_executor_read_in_own_task_dir(tmp_env):
    sched.remember_session_run("sess-1", "run-R")
    sched.top_up_tasks("run-R", target=1, max_tasks=100)
    _start_executor("run-R", "agent-20")
    payload = _make_task_executor_payload(
        "agent-20",
        "Read",
        {"file_path": "runs/run-R/tasks/task_001/direction.txt"},
        "run-R",
        "task_001",
    )
    out = hook_manager.dispatch("claude", "pre_tool_use", payload)
    assert out is not None
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_pre_tool_use_bypasses_task_executor_read_outside_runs(tmp_env):
    # Reads outside runs/ are not decided by the gate; the hook stays silent.
    sched.remember_session_run("sess-1", "run-R")
    sched.top_up_tasks("run-R", target=1, max_tasks=100)
    _start_executor("run-R", "agent-20")
    payload = _make_task_executor_payload(
        "agent-20",
        "Read",
        {"file_path": "benchmarks/skills/foo/SKILL.md"},
        "run-R",
        "task_001",
    )
    out = hook_manager.dispatch("claude", "pre_tool_use", payload)
    assert out is None


def test_pre_tool_use_denies_task_executor_read_evaluator(tmp_env):
    sched.remember_session_run("sess-1", "run-R")
    sched.top_up_tasks("run-R", target=1, max_tasks=100)
    _start_executor("run-R", "agent-21")
    payload = _make_task_executor_payload(
        "agent-21",
        "Read",
        {"file_path": "benchmarks/skills/foo/scripts/evaluator.py"},
        "run-R",
        "task_001",
    )
    out = hook_manager.dispatch("claude", "pre_tool_use", payload)
    assert out is not None
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_pre_tool_use_bypass_for_main_agent_returns_none(tmp_env):
    sched.remember_session_run("sess-main", "run-R")
    payload = {
        "session_id": "sess-main",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    }
    out = hook_manager.dispatch("claude", "pre_tool_use", payload)
    assert out is None


def _agent_payload(tool_input, session_id="sess-1"):
    """PreToolUse payload of an orchestrator Agent call."""
    return {
        "session_id": session_id,
        "hook_event_name": "PreToolUse",
        "tool_name": "Agent",
        "tool_input": tool_input,
    }


def test_executor_spawn_with_isolation_stacks_task_injection_strip_and_allow(tmp_env):
    # Three handlers touch this one call; each must build on the previous result.
    sched.remember_session_run("sess-1", "run-Z")
    sched.top_up_tasks("run-Z", target=1, max_tasks=100)

    out = hook_manager.dispatch(
        "claude", "pre_tool_use", _agent_payload(_spawn_tool_input() | {"isolation": "worktree"})
    )

    assert out is not None
    specific = out["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    assert specific["permissionDecision"] == "allow"
    updated = specific["updatedInput"]
    assert "isolation" not in updated
    assert updated["description"] == "task_001"
    assert updated["prompt"].endswith("\nrun_id:run-Z\ntask_id:task_001\n")
    assert updated["subagent_type"] == "evotrias-task-executor"
    assert updated["run_in_background"] is True


def test_isolation_stripped_for_non_executor_agent_in_run(tmp_env):
    sched.remember_session_run("sess-1", "run-Z")
    tool_input = {
        "description": "Generate directions",
        "isolation": "worktree",
        "model": "sonnet",
        "prompt": "run_id: example",
        "subagent_type": "evotrias-init-explorer",
    }

    out = hook_manager.dispatch("claude", "pre_tool_use", _agent_payload(tool_input))

    assert out is not None
    specific = out["hookSpecificOutput"]
    assert specific["permissionDecision"] == "allow"
    assert specific["updatedInput"] == {k: v for k, v in tool_input.items() if k != "isolation"}


def test_isolation_stripped_outside_any_run_without_permission_decision(tmp_env):
    tool_input = {"isolation": "worktree", "prompt": "p", "subagent_type": "evotrias-meta-guidance-generator"}

    out = hook_manager.dispatch("claude", "pre_tool_use", _agent_payload(tool_input, session_id="sess-unknown"))

    assert out is not None
    specific = out["hookSpecificOutput"]
    assert specific["updatedInput"] == {"prompt": "p", "subagent_type": "evotrias-meta-guidance-generator"}
    assert "permissionDecision" not in specific


def test_isolation_left_alone_for_unknown_agents(tmp_env):
    sched.remember_session_run("sess-1", "run-Z")

    out = hook_manager.dispatch(
        "claude", "pre_tool_use", _agent_payload({"isolation": "worktree", "subagent_type": "Explore"})
    )

    assert out is None


def test_known_agent_dispatch_without_isolation_is_only_allowed(tmp_env):
    sched.remember_session_run("sess-1", "run-Z")

    out = hook_manager.dispatch(
        "claude", "pre_tool_use", _agent_payload({"prompt": "p", "subagent_type": "evotrias-init-explorer"})
    )

    assert out is not None
    specific = out["hookSpecificOutput"]
    assert specific["permissionDecision"] == "allow"
    assert "updatedInput" not in specific

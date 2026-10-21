# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Tests for evotrias.hooks.opencode glue (scheduler).

Payload fixtures mirror structures captured from opencode 1.18.21
(see experimental/opencode_draft/design_draft.md, facts baseline).
"""

from __future__ import annotations

import importlib
import os

import pytest

from evotrias import scheduler as sched
from evotrias.db import paths
from evotrias.db.agent_index import AgentInfo
from evotrias.db.attempts import AttemptRecord, Attempts
from evotrias.db.session_map import SessionMap
from evotrias.enums_and_consts import Agents
from evotrias.hooks import broker, hook_manager
from evotrias.hooks.opencode import scheduler_hooks as oc_glue


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
    # .evotrias is cwd-relative: chdir into the temp repo so db state, runs/
    # and the opencode registry all land there.
    monkeypatch.chdir(repo)
    # Reset handler registry and re-import the glue module so its @register
    # side effects repopulate the broker against a clean slate.
    broker._handlers.clear()
    importlib.reload(oc_glue)
    return repo


def _spawn_args(subagent="evotrias-task-executor"):
    """Original task-tool args of an orchestrator spawning an executor."""
    return {
        "description": "Execute replenishment task",
        "prompt": "task_description: circle packing\nmax_attempts: 3",
        "subagent_type": subagent,
        "background": True,
    }


def _before_payload(call_id="call_1", session="ses_parent", subagent="evotrias-task-executor"):
    return {
        "input": {"tool": "task", "sessionID": session, "callID": call_id},
        "output": {"args": _spawn_args(subagent)},
    }


def _after_payload(call_id="call_1", session="ses_parent", child="ses_child"):
    # input.args is deliberately the pre-mutation snapshot (observed opencode
    # behavior): it never carries the injected task_id.
    return {
        "input": {"tool": "task", "sessionID": session, "callID": call_id, "args": _spawn_args()},
        "output": {
            "title": "Execute replenishment task",
            "metadata": {
                "parentSessionId": session,
                "sessionId": child,
                "model": {"modelID": "GLM-5.2", "providerID": "opencsi"},
                "background": True,
                "jobId": child,
                "truncated": False,
            },
            "output": f'<task id="{child}" state="running">started</task>',
        },
    }


def _created_payload(child="ses_child", parent="ses_parent", agent="evotrias-task-executor"):
    return {
        "id": "evt_created",
        "type": "session.created",
        "properties": {
            "sessionID": child,
            "info": {
                "id": child,
                "slug": "hidden-panda",
                "version": "1.18.21",
                "projectID": "proj",
                "directory": "D:\\ws",
                "path": "ws",
                "parentID": parent,
                "title": f"task_001 (@{agent} subagent)",
                "agent": agent,
                "permission": [],
                "cost": 0,
            },
        },
    }


def _idle_payload(child="ses_child"):
    return {"id": "evt_idle", "type": "session.idle", "properties": {"sessionID": child}}


def test_configure_run_after_records_session_run(tmp_env):
    payload = {
        "input": {
            "tool": "evotrias_configure_run",
            "sessionID": "ses-9",
            "callID": "c0",
            "args": {"run_id": "run-9", "max_total_tasks": 50},
        },
        "output": {
            "content": [{"type": "text", "text": "{}"}],
            "structuredContent": {"configured": True},
            "isError": False,
        },
    }
    out = hook_manager.dispatch("opencode", "tool.execute.after", payload)
    assert out is None
    assert sched.lookup_run_by_session("ses-9") == "run-9"


def test_configure_run_after_without_session_id_registers_nothing(tmp_env):
    # A bare input_.get("sessionID") used to hand None to the scheduler,
    # which str()-ed it into a literal "None" session key.
    payload = {
        "input": {"tool": "evotrias_configure_run", "callID": "c0", "args": {"run_id": "run-9", "max_total_tasks": 50}},
        "output": {"content": [], "structuredContent": {"configured": True}, "isError": False},
    }
    out = hook_manager.dispatch("opencode", "tool.execute.after", payload)
    assert out is None
    assert SessionMap().sessions == {}


def test_executor_full_chain_observed_order(tmp_env):
    # Observed opencode ordering: before -> session.created -> after -> idle.
    sched.remember_session_run("ses_parent", "run-Z")
    sched.top_up_tasks("run-Z", target=2, max_tasks=100)

    # 1. before: allocate a task and rewrite args (result is the native args subtree).
    out = hook_manager.dispatch("opencode", "tool.execute.before", _before_payload())
    assert out is not None
    args = out["args"]
    assert args["description"] == "task_001"
    assert args["prompt"].endswith("\nrun_id:run-Z\ntask_id:task_001\n")
    assert args["subagent_type"] == "evotrias-task-executor"
    assert args["background"] is True
    snap = sched.pool_snapshot("run-Z")
    assert snap.created == ["task_002"]
    assert snap.pending == ["task_001"]

    # 2. session.created (fires between before and after): index with no task yet.
    out = hook_manager.dispatch("opencode", "session.created", _created_payload())
    assert out is None
    assert sched.lookup_agent("run-Z", "ses_child") == AgentInfo(Agents.TASK_EXECUTOR, {"task_id": None})

    # 3. after: bind via callID registry, then backfill the index to running.
    out = hook_manager.dispatch("opencode", "tool.execute.after", _after_payload())
    assert out is None
    assert sched.lookup_agent("run-Z", "ses_child") == AgentInfo(Agents.TASK_EXECUTOR, {"task_id": "task_001"})
    snap = sched.pool_snapshot("run-Z")
    assert snap.queued == {}
    assert _agents(snap.running) == {"task_001": "ses_child"}

    # 4. idle: terminal accounting from the recorded successful attempt.
    _record_success("run-Z", "task_001", 0.91)
    out = hook_manager.dispatch("opencode", "session.idle", _idle_payload())
    assert out is None
    snap = sched.pool_snapshot("run-Z")
    assert _fitness(snap.completed) == {"task_001": 0.91}
    assert snap.running == {}

    # 5. the registry entry is consumed: a second idle is a no-op.
    hook_manager.dispatch("opencode", "session.idle", _idle_payload())
    assert sched.pool_snapshot("run-Z").failed == {}


def test_executor_full_chain_created_after_after(tmp_env):
    # Alternative ordering: before -> after -> session.created -> idle.
    sched.remember_session_run("ses_parent", "run-R")
    sched.top_up_tasks("run-R", target=1, max_tasks=100)

    hook_manager.dispatch("opencode", "tool.execute.before", _before_payload())
    hook_manager.dispatch("opencode", "tool.execute.after", _after_payload())
    snap = sched.pool_snapshot("run-R")
    assert _agents(snap.queued) == {"task_001": "ses_child"}
    assert sched.lookup_agent("run-R", "ses_child") is None

    hook_manager.dispatch("opencode", "session.created", _created_payload())
    assert sched.lookup_agent("run-R", "ses_child") == AgentInfo(Agents.TASK_EXECUTOR, {"task_id": "task_001"})
    snap = sched.pool_snapshot("run-R")
    assert _agents(snap.running) == {"task_001": "ses_child"}

    # No completion record: idle fails the task.
    hook_manager.dispatch("opencode", "session.idle", _idle_payload())
    snap = sched.pool_snapshot("run-R")
    assert "task_001" in snap.failed
    assert snap.failed["task_001"].reason == "no successful attempt recorded on session idle"


def test_non_executor_spawn_passes_through(tmp_env):
    sched.remember_session_run("ses_parent", "run-S")
    sched.top_up_tasks("run-S", target=1, max_tasks=100)
    out = hook_manager.dispatch("opencode", "tool.execute.before", _before_payload(subagent="story_writer"))
    assert out is None
    assert sched.pool_snapshot("run-S").created == ["task_001"]


def test_before_and_after_skip_when_no_run_for_session(tmp_env):
    out = hook_manager.dispatch("opencode", "tool.execute.before", _before_payload(session="ses_unknown"))
    assert out is None
    # after finds no call binding and must not touch any pool.
    out = hook_manager.dispatch("opencode", "tool.execute.after", _after_payload(session="ses_unknown"))
    assert out is None


def test_created_without_parent_is_ignored(tmp_env):
    sched.remember_session_run("ses_parent", "run-M")
    payload = _created_payload(child="ses_main", agent="build")
    del payload["properties"]["info"]["parentID"]
    out = hook_manager.dispatch("opencode", "session.created", payload)
    assert out is None
    assert sched.lookup_agent("run-M", "ses_main") is None


def test_created_skips_when_parent_has_no_run(tmp_env):
    out = hook_manager.dispatch("opencode", "session.created", _created_payload(parent="ses_unknown"))
    assert out is None
    assert sched.lookup_agent("run-any", "ses_child") is None


def test_non_executor_children_indexed_and_idle_is_harmless(tmp_env):
    sched.remember_session_run("ses_parent", "run-mixed")
    sched.top_up_tasks("run-mixed", target=1, max_tasks=100)
    expected = {
        "evotrias-init-explorer": AgentInfo(Agents.INIT_EXPLORER),
        "evotrias-meta-guidance-generator": AgentInfo(Agents.META_GUIDANCE_GENERATOR),
        "general": AgentInfo(Agents.UNKNOWN, {"origin_agent_type": "general"}),
    }
    for agent, want in expected.items():
        child = f"ses_{agent}"
        out = hook_manager.dispatch("opencode", "session.created", _created_payload(child=child, agent=agent))
        assert out is None, f"{agent} must get no task injection"
        assert sched.lookup_agent("run-mixed", child) == want

    snap = sched.pool_snapshot("run-mixed")
    assert snap.created == ["task_001"], "created pool must be untouched"
    assert snap.running == {}

    # Their idles pop the registry without failing anything.
    for agent in expected:
        hook_manager.dispatch("opencode", "session.idle", _idle_payload(f"ses_{agent}"))
    assert sched.pool_snapshot("run-mixed").failed == {}


def test_idle_for_unregistered_session_is_noop(tmp_env):
    out = hook_manager.dispatch("opencode", "session.idle", _idle_payload("ses_never_seen"))
    assert out is None


def test_registry_round_trips_typed_entries(tmp_env):
    with oc_glue._Registry() as registry:
        registry.calls["call_1"] = oc_glue.PendingCall(run_id="run-Z", task_id="task_001")
        registry.sessions["ses_child"] = oc_glue.PendingSession(agent="evotrias-task-executor", parent_id="ses_parent")
    reloaded = oc_glue._Registry()
    assert reloaded.calls["call_1"] == oc_glue.PendingCall(run_id="run-Z", task_id="task_001")
    assert isinstance(reloaded.sessions["ses_child"], oc_glue.PendingSession)
    assert reloaded.sessions["ses_child"].parent_id == "ses_parent"
    assert paths.OPENCODE_REGISTRY.exists()
    with oc_glue._Registry() as registry:
        assert registry.calls.pop("call_1", None) is not None
        assert registry.calls.pop("missing", None) is None
    assert oc_glue._Registry().calls == {}

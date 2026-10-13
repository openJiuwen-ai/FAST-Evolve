"""Tests for evotrias.db.task_pool, evotrias.db.agent_index, evotrias.db.session_map."""

from __future__ import annotations

import json

import pytest

from evotrias.db import paths
from evotrias.db.agent_index import AgentIndex, AgentInfo
from evotrias.db.session_map import SessionMap
from evotrias.db.task_pool import TaskAssigned, TaskCompleted, TaskFailed, TaskPool
from evotrias.enums_and_consts import Agents

RUN_ID = "pool_store_test"


@pytest.fixture(autouse=True)
def cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_task_pool_empty_and_next_task_id_persists_only_inside_with():
    pool = TaskPool(RUN_ID)
    assert (pool.created, pool.pending, pool.queued, pool.running, pool.completed, pool.failed) == (
        [],
        [],
        {},
        {},
        {},
        {},
    )
    assert pool.next_seq == 1
    assert pool.next_task_id() == "task_001"
    assert not (paths.run_dir(RUN_ID) / paths.TASK_POOL).exists()
    with TaskPool(RUN_ID) as pool:
        assert pool.next_task_id() == "task_001"
        assert pool.next_task_id() == "task_002"
        pool.created.extend(["task_001", "task_002"])
    reloaded = TaskPool(RUN_ID)
    assert reloaded.next_seq == 3
    assert reloaded.created == ["task_001", "task_002"]
    assert set(json.loads((paths.run_dir(RUN_ID) / paths.TASK_POOL).read_text(encoding="utf-8"))) >= {
        "created",
        "pending",
        "queued",
        "running",
        "completed",
        "failed",
        "next_seq",
    }


def test_task_pool_buckets_round_trip_as_typed_records():
    with TaskPool(RUN_ID) as pool:
        pool.queued["task_001"] = TaskAssigned(agent_id="agent-1", at="2026-01-01T00:00:00+00:00")
        pool.running["task_002"] = TaskAssigned(agent_id="agent-2")
        pool.completed["task_003"] = TaskCompleted(fitness=0.9)
        pool.failed["task_004"] = TaskFailed(reason="boom")
    reloaded = TaskPool(RUN_ID)
    assert reloaded.queued == {"task_001": TaskAssigned(agent_id="agent-1", at="2026-01-01T00:00:00+00:00")}
    assert isinstance(reloaded.running["task_002"], TaskAssigned)
    assert reloaded.running["task_002"].agent_id == "agent-2" and reloaded.running["task_002"].at
    assert isinstance(reloaded.completed["task_003"], TaskCompleted)
    assert reloaded.completed["task_003"].fitness == 0.9
    assert isinstance(reloaded.failed["task_004"], TaskFailed)
    assert reloaded.failed["task_004"].reason == "boom"
    on_disk = json.loads((paths.run_dir(RUN_ID) / paths.TASK_POOL).read_text(encoding="utf-8"))
    assert on_disk["completed"]["task_003"]["fitness"] == 0.9
    assert on_disk["running"]["task_002"]["agent_id"] == "agent-2"


def test_agent_index_round_trips_enum_and_attrs():
    with AgentIndex(RUN_ID) as index:
        index.agents["a1"] = AgentInfo(Agents.TASK_EXECUTOR, {"task_id": "task_001"})
        index.agents["a2"] = AgentInfo(Agents.UNKNOWN, {"origin_agent_type": "general-purpose"})
        index.agents["a3"] = AgentInfo(Agents.INIT_EXPLORER)
    on_disk = json.loads((paths.run_dir(RUN_ID) / paths.AGENT_INDEX).read_text(encoding="utf-8"))
    assert on_disk["agents"]["a1"] == {"agent_type": "task_executor", "attrs": {"task_id": "task_001"}}
    reloaded = AgentIndex(RUN_ID)
    a1 = reloaded.agents["a1"]
    assert isinstance(a1, AgentInfo) and a1.agent_type is Agents.TASK_EXECUTOR
    assert a1.attrs == {"task_id": "task_001"}
    assert reloaded.agents["a2"] == AgentInfo(Agents.UNKNOWN, {"origin_agent_type": "general-purpose"})
    assert reloaded.agents["a3"] == AgentInfo(Agents.INIT_EXPLORER)
    assert reloaded.agents.get("missing") is None


def test_session_map_is_global_and_round_trips():
    assert SessionMap().sessions == {}
    with SessionMap() as sessions:
        sessions.sessions["ses-1"] = "run-A"
        sessions.sessions["ses-2"] = "run-B"
    assert SessionMap().sessions == {"ses-1": "run-A", "ses-2": "run-B"}
    assert paths.SESSION_MAP.exists()
    assert paths.SESSION_MAP.parent == paths.DB_ROOT

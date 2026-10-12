"""Tests for evotrias.scheduler."""

from __future__ import annotations

import pytest

from evotrias import scheduler as sched
from evotrias.db import paths
from evotrias.db.agent_index import AgentInfo
from evotrias.enums import Agents


def _agents(bucket):
    """queued/running -> {task_id: agent_id}, dropping the timestamp."""
    return {tid: held.agent_id for tid, held in bucket.items()}


def _fitness(bucket):
    return {tid: done.fitness for tid, done in bucket.items()}


def _reasons(bucket):
    return {tid: failed.reason for tid, failed in bucket.items()}


@pytest.fixture
def tmp_state(monkeypatch, tmp_path):
    # .evotrias is cwd-relative: chdir into a temp repo so no state leaks.
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    return repo


def _claim(run_id, agent_id):
    """Walk one task through the full allocate -> queue -> start chain."""
    task_id = sched.allocate_task(run_id)
    sched.queue_task(run_id, task_id, agent_id)
    return sched.start_task(run_id, agent_id)


def test_session_map_roundtrip(tmp_state):
    assert sched.lookup_run_by_session("sess-1") is None
    sched.remember_session_run("sess-1", "run-42")
    assert sched.lookup_run_by_session("sess-1") == "run-42"


def test_top_up_tasks_creates_ids(tmp_state):
    created = sched.top_up_tasks("run-A", target=3, max_tasks=100)
    assert created == ["task_001", "task_002", "task_003"]
    # idempotent: already at target
    assert sched.top_up_tasks("run-A", target=3, max_tasks=100) == []
    snap = sched.pool_snapshot("run-A")
    assert snap.created == ["task_001", "task_002", "task_003"]
    assert snap.pending == []
    assert snap.queued == {}
    assert snap.running == {}


def test_top_up_tasks_caps_at_max_tasks(tmp_state):
    # The id sequence is monotonic, so finished tasks still consume the budget:
    # topping up must never mint a task_id past max_tasks.
    assert sched.top_up_tasks("run-A2", target=2, max_tasks=3) == ["task_001", "task_002"]
    for task_id in ("task_001", "task_002"):
        _claim("run-A2", "a1")
        sched.complete_task("run-A2", task_id, fitness=0.5)
    # 2 of 3 spent; a concurrency-2 top-up may only create the last one.
    assert sched.top_up_tasks("run-A2", target=2, max_tasks=3) == ["task_003"]
    _claim("run-A2", "a1")
    sched.fail_task("run-A2", "task_003", reason="boom")
    assert sched.top_up_tasks("run-A2", target=2, max_tasks=3) == []


def test_allocate_task_transitions_to_pending(tmp_state):
    sched.top_up_tasks("run-B", target=2, max_tasks=100)
    assert sched.allocate_task("run-B") == "task_001"
    snap = sched.pool_snapshot("run-B")
    assert snap.created == ["task_002"]
    assert snap.pending == ["task_001"]


def test_allocate_task_empty_pool_returns_none(tmp_state):
    # Empty pool is CRITICAL: allocate returns None, caller denies the spawn.
    assert sched.allocate_task("run-C") is None


def test_queue_task_binds_agent(tmp_state):
    sched.top_up_tasks("run-B2", target=1, max_tasks=100)
    task = sched.allocate_task("run-B2")
    sched.queue_task("run-B2", task, "agent-7")
    snap = sched.pool_snapshot("run-B2")
    assert snap.pending == []
    assert _agents(snap.queued) == {"task_001": "agent-7"}


def test_queue_task_rejects_when_not_pending(tmp_state):
    # Precondition violation must not write: the task stays where it was and
    # no phantom entry lands in queued.
    sched.top_up_tasks("run-B5", target=1, max_tasks=100)
    sched.queue_task("run-B5", "task_001", "agent-7")  # still in created
    sched.queue_task("run-B5", "task_bogus", "agent-7")  # never existed
    snap = sched.pool_snapshot("run-B5")
    assert snap.created == ["task_001"]
    assert snap.queued == {}


def test_start_task_transitions_to_running(tmp_state):
    sched.top_up_tasks("run-B3", target=2, max_tasks=100)
    sched.queue_task("run-B3", sched.allocate_task("run-B3"), "agent-7")
    sched.queue_task("run-B3", sched.allocate_task("run-B3"), "agent-8")
    # reverse lookup by agent_id picks the right task
    assert sched.start_task("run-B3", "agent-8") == "task_002"
    snap = sched.pool_snapshot("run-B3")
    assert _agents(snap.queued) == {"task_001": "agent-7"}
    assert _agents(snap.running) == {"task_002": "agent-8"}


def test_start_task_unknown_agent_returns_none(tmp_state):
    sched.top_up_tasks("run-B4", target=1, max_tasks=100)
    sched.queue_task("run-B4", sched.allocate_task("run-B4"), "agent-7")
    assert sched.start_task("run-B4", "agent-never-bound") is None


def test_complete_task_stores_fitness(tmp_state):
    sched.top_up_tasks("run-D", target=1, max_tasks=100)
    task = _claim("run-D", "a1")
    sched.complete_task("run-D", task, fitness=0.875)
    snap = sched.pool_snapshot("run-D")
    assert snap.running == {}
    assert _fitness(snap.completed) == {task: 0.875}


def test_fail_task_stores_reason(tmp_state):
    sched.top_up_tasks("run-E", target=1, max_tasks=100)
    task = _claim("run-E", "a2")
    sched.fail_task("run-E", task, reason="verifier crashed")
    snap = sched.pool_snapshot("run-E")
    assert snap.running == {}
    assert _reasons(snap.failed) == {task: "verifier crashed"}


def test_complete_task_rejects_when_not_running(tmp_state):
    sched.top_up_tasks("run-D2", target=1, max_tasks=100)
    task = sched.allocate_task("run-D2")
    sched.queue_task("run-D2", task, "a1")
    sched.complete_task("run-D2", task, fitness=0.5)  # queued, never started
    snap = sched.pool_snapshot("run-D2")
    assert _agents(snap.queued) == {task: "a1"}
    assert snap.completed == {}


def test_fail_task_rejects_when_not_running(tmp_state):
    sched.top_up_tasks("run-E2", target=1, max_tasks=100)
    task = sched.allocate_task("run-E2")
    sched.fail_task("run-E2", task, reason="boom")  # pending, never started
    snap = sched.pool_snapshot("run-E2")
    assert snap.pending == [task]
    assert snap.failed == {}


def test_transitions_on_unknown_run_do_not_create_a_pool(tmp_state):
    # Rejection happens before save, so a bad run_id must not mint a pool file.
    sched.complete_task("run-ghost", "task_001", fitness=1.0)
    sched.fail_task("run-ghost", "task_001", reason="boom")
    sched.queue_task("run-ghost", "task_001", "a1")
    assert not paths.run_dir("run-ghost").exists()


def test_remember_agent_stores_agent_info_verbatim(tmp_state):
    # The caller owns the shape; the scheduler stores and returns it unchanged,
    # except that agent_type round-trips as an enum through the JSON str.
    sched.remember_session_run("sess-2", "run-F")
    sched.top_up_tasks("run-F", target=1, max_tasks=100)
    task = _claim("run-F", "agent-99")
    info = AgentInfo(Agents.TASK_EXECUTOR, {"task_id": task})
    sched.remember_agent("run-F", "agent-99", info)
    assert sched.lookup_agent("run-F", "agent-99") == info


def test_remember_agent_accepts_a_run_scoped_info_without_task_id(tmp_state):
    # Run-scoped agents (init-explorer, meta-guidance-generator, …) are indexed
    # so the permission layer can resolve them, but they hold no task.
    sched.remember_session_run("sess-3", "run-F2")
    info = AgentInfo(Agents.INIT_EXPLORER)
    sched.remember_agent("run-F2", "agent-explorer", info)
    stored = sched.lookup_agent("run-F2", "agent-explorer")
    assert stored == info
    assert stored is not None and "task_id" not in stored.attrs, "a run-scoped agent must not carry a task"


def test_remember_agent_stores_unknown_with_its_origin_type(tmp_state):
    # UNKNOWN collapses every unrecognized type, so the raw payload string is
    # what tells one from another.
    info = AgentInfo(Agents.UNKNOWN, {"origin_agent_type": "general-purpose"})
    sched.remember_agent("run-F5", "agent-gp", info)
    assert sched.lookup_agent("run-F5", "agent-gp") == info


def test_lookup_agent_returns_none_for_unknown_agent(tmp_state):
    sched.top_up_tasks("run-F3", target=1, max_tasks=100)
    assert sched.lookup_agent("run-F3", "never-indexed") is None


def test_lookup_agent_is_scoped_to_its_run(tmp_state):
    sched.remember_agent("run-one", "agent-shared", AgentInfo(Agents.INIT_EXPLORER))
    assert sched.lookup_agent("run-one", "agent-shared") is not None
    assert sched.lookup_agent("run-two", "agent-shared") is None


def test_transitions_do_not_index(tmp_state):
    # Indexing is the caller's job: every agent gets indexed, task-holding or not,
    # so no pool transition may do half of it behind the caller's back.
    sched.top_up_tasks("run-F4", target=1, max_tasks=100)
    _claim("run-F4", "agent-unindexed")
    assert sched.lookup_agent("run-F4", "agent-unindexed") is None


def test_lookup_agent_retained_after_complete(tmp_state):
    # agent_index is retained after termination for retroactive lookup.
    sched.top_up_tasks("run-G", target=1, max_tasks=100)
    task = _claim("run-G", "agent-100")
    info = AgentInfo(Agents.TASK_EXECUTOR, {"task_id": task})
    sched.remember_agent("run-G", "agent-100", info)
    sched.complete_task("run-G", task, fitness=1.0)
    assert sched.lookup_agent("run-G", "agent-100") == info


def test_running_count(tmp_state):
    sched.top_up_tasks("run-H", target=2, max_tasks=100)
    assert sched.running_count("run-H") == 0
    _claim("run-H", "a3")
    assert sched.running_count("run-H") == 1

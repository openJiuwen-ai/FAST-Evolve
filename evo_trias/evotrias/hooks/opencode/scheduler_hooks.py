"""Scheduler hooks for the opencode harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evotrias import scheduler
from evotrias.db import paths
from evotrias.db.agent_index import AgentInfo
from evotrias.db.attempts import Attempts
from evotrias.db.base import DBObject
from evotrias.enums_and_consts import Agents
from evotrias.hooks import hook_manager
from evotrias.logger import logger

_CONFIGURE_RUN_TOOL = "evotrias_configure_run"
_TASK_TOOL = "task"

_AGENT_TYPE_MAP: dict[str, Agents] = {
    "evotrias-task-executor": Agents.TASK_EXECUTOR,
    "evotrias-init-explorer": Agents.INIT_EXPLORER,
    "evotrias-meta-guidance-generator": Agents.META_GUIDANCE_GENERATOR,
}


@dataclass
class PendingCall:
    """A task-tool call seen at ``tool.execute.before``, awaiting its ``after``."""

    run_id: str
    task_id: str


@dataclass
class PendingSession:
    """A subagent session seen at ``session.created``, awaiting its ``idle``."""

    agent: str
    parent_id: str


class _Registry(DBObject):
    """Cross-process store stitching hook events: before->after (calls), created->idle (sessions).

    Private to the opencode hooks, so it lives here rather than in ``evotrias.db``;
    it still borrows the db base class for atomic JSON IO.
    """

    calls: dict[str, PendingCall] = field(default_factory=dict)
    sessions: dict[str, PendingSession] = field(default_factory=dict)

    def __init__(self):
        super().__init__(paths.OPENCODE_REGISTRY)


def _spawn_args(payload: dict) -> dict[str, Any]:
    """Task-tool args: before carries them in output.args, after in input.args."""
    output = payload.get("output") or {}
    if isinstance(output.get("args"), dict):
        return output["args"]
    return (payload.get("input") or {}).get("args") or {}


def _is_executor_spawn(payload: dict) -> bool:
    """True when the tool call spawns a task-executor subagent."""
    if (payload.get("input") or {}).get("tool") != _TASK_TOOL:
        return False
    raw = str(_spawn_args(payload).get("subagent_type") or "").strip().lower()
    return _AGENT_TYPE_MAP.get(raw) == Agents.TASK_EXECUTOR


@hook_manager.register("opencode", "tool.execute.before", priority=0)
def executor_allocate_task(payload: dict, result: dict, extra_data: Any) -> dict[str, Any] | None:
    if not _is_executor_spawn(payload):
        return result

    input_ = payload.get("input") or {}
    session_id = str(input_.get("sessionID") or "")
    run_id = scheduler.lookup_run_by_session(session_id)
    if not run_id:
        return result

    task_id = scheduler.allocate_task(run_id)
    if task_id is None:
        logger.critical(
            f"scheduler hook: no created task available (created pool exhausted — "
            f"check_convergence should have topped it up) run={run_id}"
        )
        return result

    call_id = str(input_.get("callID") or "")
    with _Registry() as registry:
        registry.calls[call_id] = PendingCall(run_id=run_id, task_id=task_id)

    args = dict(_spawn_args(payload))
    args["description"] = task_id
    args["prompt"] = str(args.get("prompt") or "") + f"\nrun_id:{run_id}\ntask_id:{task_id}\n"
    logger.info(f"scheduler hook: executor spawn allocated run={run_id} task={task_id}")
    logger.debug(f"{_spawn_args(payload)=!r} -> {args=!r}")
    return result | {"args": args}


@hook_manager.register("opencode", "tool.execute.after", priority=0)
def remember_session_run(payload: dict, result: dict, extra_data: Any) -> dict[str, Any] | None:
    input_ = payload.get("input") or {}
    if input_.get("tool") != _CONFIGURE_RUN_TOOL:
        return result

    run_id = str((input_.get("args") or {}).get("run_id") or "")
    session_id = str(input_.get("sessionID") or "")
    scheduler.remember_session_run(session_id, run_id)
    return result


@hook_manager.register("opencode", "tool.execute.after", priority=0)
def executor_bind_agent(payload: dict, result: dict, extra_data: Any) -> dict[str, Any] | None:
    if not _is_executor_spawn(payload):
        return result

    input_ = payload.get("input") or {}
    with _Registry() as registry:
        call = registry.calls.pop(str(input_.get("callID") or ""), None)
    if call is None:
        # No allocation happened for this call (unknown session or empty pool).
        return result
    run_id, task_id = call.run_id, call.task_id

    metadata = (payload.get("output") or {}).get("metadata") or {}
    agent_id = str(metadata.get("sessionId") or "")
    if not agent_id:
        logger.error(
            f"scheduler hook: executor spawn response missing metadata.sessionId " f"run={run_id} task={task_id}"
        )
        return result

    scheduler.queue_task(run_id, task_id, agent_id)

    # session.created usually fires between before and after, leaving the
    # executor indexed with task_id=None; backfill it now.
    agent_info = scheduler.lookup_agent(run_id, agent_id)
    if agent_info is not None and agent_info.attrs.get("task_id") is None:
        agent_info.attrs["task_id"] = scheduler.start_task(run_id, agent_id)
        scheduler.remember_agent(run_id, agent_id, agent_info)
        logger.info(
            f"scheduler hook: backfilled task_id={agent_info.attrs['task_id']} " f"for agent {agent_id} in run {run_id}"
        )
    return result


@hook_manager.register("opencode", "session.created", priority=10)
def remember_agent(payload: dict, result: dict, extra_data: Any) -> dict[str, Any] | None:
    info = ((payload.get("properties") or {}).get("info")) or {}
    parent_id = str(info.get("parentID") or "")
    if not parent_id:
        # Not a subagent session (main sessions never carry parentID).
        return result

    run_id = scheduler.lookup_run_by_session(parent_id)
    if not run_id:
        return result

    session_id = str(info.get("id") or "")
    agent_name = str(info.get("agent") or "").strip().lower()
    agent_type = _AGENT_TYPE_MAP.get(agent_name, Agents.UNKNOWN)

    with _Registry() as registry:
        registry.sessions[session_id] = PendingSession(agent=agent_name, parent_id=parent_id)

    agent_info = AgentInfo(agent_type=agent_type)
    if agent_type == Agents.UNKNOWN:
        agent_info.attrs["origin_agent_type"] = info.get("agent")
    elif agent_type == Agents.TASK_EXECUTOR:
        # queued -> running; None when this fires before executor_bind_agent
        # (usual opencode ordering), which then backfills the index itself
        agent_info.attrs["task_id"] = scheduler.start_task(run_id, session_id)

    scheduler.remember_agent(run_id, session_id, agent_info)
    return result


@hook_manager.register("opencode", "session.idle", priority=0)
def executor_report_complete(payload: dict, result: dict, extra_data: Any) -> dict[str, Any] | None:
    session_id = str(((payload.get("properties") or {}).get("sessionID")) or "")
    with _Registry() as registry:
        entry = registry.sessions.pop(session_id, None)
    if entry is None:
        # Main session or a subagent outside any evolve run.
        return result

    if _AGENT_TYPE_MAP.get(entry.agent) != Agents.TASK_EXECUTOR:
        return result

    run_id = scheduler.lookup_run_by_session(entry.parent_id)
    if not run_id:
        return result

    agent_info = scheduler.lookup_agent(run_id, session_id)
    if agent_info is None:
        logger.error(f"scheduler hook: failed to find agent info run={run_id} agent={session_id}")
        return result

    task_id = agent_info.attrs.get("task_id")
    if not task_id:
        logger.error(f"scheduler hook: agent {session_id} has no task_id in run {run_id}")
        return result

    success = Attempts(run_id).successes.get(task_id)
    if success is not None:
        fitness = success.verification_result.get("fitness")
        scheduler.complete_task(run_id, task_id, fitness)
        logger.info(f"scheduler hook: session idle complete run={run_id} task={task_id} fitness={fitness!r}")
    else:
        reason = "no successful attempt recorded on session idle"
        scheduler.fail_task(run_id, task_id, reason)
        logger.info(f"scheduler hook: session idle fail run={run_id} task={task_id} reason={reason!r}")
    return result

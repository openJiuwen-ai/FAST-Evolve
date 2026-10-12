from __future__ import annotations

from typing import Any

from evotrias import scheduler
from evotrias.db.agent_index import AgentInfo
from evotrias.db.attempts import Attempts
from evotrias.enums import Agents
from evotrias.hooks import hook_manager
from evotrias.logger import logger

_CONFIGURE_RUN_TOOL = "mcp__plugin_evotrias_plugin_evotrias__configure_run"

_AGENT_TYPE_MAP: dict[str, Agents] = {
    "evotrias_plugin:task-executor": Agents.TASK_EXECUTOR,
    "evotrias_plugin:init-explorer": Agents.INIT_EXPLORER,
    "evotrias_plugin:meta-guidance-generator": Agents.META_GUIDANCE_GENERATOR,
}


def _resolve_agent_type(payload: dict) -> Agents:
    """Classify a hook payload's agent. No agent_id means the orchestrator itself."""
    if not payload.get("agent_id"):
        return Agents.MAIN
    raw = str(payload.get("agent_type") or "").strip().lower()
    if raw and raw in _AGENT_TYPE_MAP:
        return _AGENT_TYPE_MAP[raw]
    return Agents.UNKNOWN


def _is_executor_spawn(payload: dict) -> bool:
    """True when the tool call spawns a task-executor subagent."""
    if payload.get("tool_name") != "Agent":
        return False
    tool_input = payload.get("tool_input") or {}
    raw = str(tool_input.get("subagent_type") or "").strip().lower()
    return _AGENT_TYPE_MAP.get(raw) == Agents.TASK_EXECUTOR


@hook_manager.register("claude", "post_tool_use", priority=0)
def remember_session_run(payload: dict, result: dict, extra_data: Any) -> dict[str, Any] | None:
    tool_name = payload.get("tool_name")
    if tool_name != _CONFIGURE_RUN_TOOL:
        return result

    tool_input = payload.get("tool_input") or {}
    run_id = str(tool_input.get("run_id") or "")
    session_id = str(payload.get("session_id") or "")
    scheduler.remember_session_run(session_id, run_id)
    return result


@hook_manager.register("claude", "subagent_start", priority=10)
def remember_agent(payload: dict, result: dict, extra_data: Any) -> dict[str, Any] | None:
    agent_id = str(payload.get("agent_id") or "")
    session_id = str(payload.get("session_id") or "")
    agent_type = _resolve_agent_type(payload)

    run_id = scheduler.lookup_run_by_session(session_id)
    if not run_id:
        return result

    agent_info = AgentInfo(agent_type=agent_type)
    if agent_type == Agents.UNKNOWN:
        agent_info.attrs["origin_agent_type"] = payload.get("agent_type")
    elif agent_type == Agents.TASK_EXECUTOR:
        # queued -> running; None when this fires before executor_bind_agent
        # (parallel processes), which then backfills the index itself
        agent_info.attrs["task_id"] = scheduler.start_task(run_id, agent_id)

    scheduler.remember_agent(run_id, agent_id, agent_info)
    return result


@hook_manager.register("claude", "pre_tool_use", priority=0)
def executor_allocate_task(payload: dict, result: dict, extra_data: Any) -> dict[str, Any] | None:
    if not _is_executor_spawn(payload):
        return result

    session_id = str(payload.get("session_id") or "")
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
    tool_input = payload.get("tool_input") or {}
    updated = dict(tool_input)
    updated["description"] = task_id
    updated["prompt"] = str(tool_input.get("prompt") or "") + f"\nrun_id:{run_id}\ntask_id:{task_id}\n"
    logger.info(f"scheduler hook: executor spawn allocated run={run_id} task={task_id}")
    logger.debug(f"{tool_input=!r} -> {updated=!r}")
    return result | {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": updated,
        }
    }


@hook_manager.register("claude", "post_tool_use", priority=0)
def executor_bind_agent(payload: dict, result: dict, extra_data: Any) -> dict[str, Any] | None:
    if not _is_executor_spawn(payload):
        return result

    session_id = str(payload.get("session_id") or "")
    run_id = scheduler.lookup_run_by_session(session_id)
    if not run_id:
        return result

    tool_response = payload.get("tool_response") or {}
    agent_id = str(tool_response.get("agentId") or "")
    task_id = str(tool_response.get("description") or "")
    if not agent_id or not task_id:
        logger.error(
            f"scheduler hook: executor spawn response missing agentId/description " f"run={run_id} {tool_response=!r}"
        )
        return result

    scheduler.queue_task(run_id, task_id, agent_id)

    # SubagentStart may have fired before this PostToolUse (parallel processes),
    # leaving the executor indexed with task_id=None; backfill it now.
    agent_info = scheduler.lookup_agent(run_id, agent_id)
    if agent_info is not None and agent_info.attrs.get("task_id") is None:
        agent_info.attrs["task_id"] = scheduler.start_task(run_id, agent_id)
        scheduler.remember_agent(run_id, agent_id, agent_info)
        logger.info(
            f"scheduler hook: backfilled task_id={agent_info.attrs['task_id']} " f"for agent {agent_id} in run {run_id}"
        )
    return result


@hook_manager.register("claude", "subagent_stop", priority=0)
def executor_report_complete(payload: dict, result: dict, extra_data: Any) -> dict[str, Any] | None:
    agent_id = str(payload.get("agent_id") or "")
    session_id = str(payload.get("session_id") or "")
    agent_type = _resolve_agent_type(payload)

    if agent_type != Agents.TASK_EXECUTOR:
        return result

    run_id = scheduler.lookup_run_by_session(session_id)
    if not run_id:
        return result

    agent_info = scheduler.lookup_agent(run_id, agent_id)
    if agent_info is None:
        logger.error(f"scheduler hook: failed to find agent info run={run_id} agent={agent_id}")
        return result

    if agent_info.agent_type != Agents.TASK_EXECUTOR:
        return result

    task_id = agent_info.attrs.get("task_id")
    if not task_id:
        logger.error(f"scheduler hook: agent {agent_id} has no task_id in run {run_id}")
        return result

    success = Attempts(run_id).successes.get(task_id)
    if success is not None:
        fitness = success.verification_result.get("fitness")
        scheduler.complete_task(run_id, task_id, fitness)
        logger.info(f"scheduler hook: subagent_stop complete run={run_id} task={task_id} fitness={fitness!r}")
    else:
        last_msg = str(payload.get("last_assistant_message") or "")
        reason = last_msg[:200] if last_msg else "no successful attempt recorded and no assistant message"
        scheduler.fail_task(run_id, task_id, reason)
        logger.info(f"scheduler hook: subagent_stop fail run={run_id} task={task_id} reason={reason[:80]!r}")
    return result

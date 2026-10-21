# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
from __future__ import annotations

from typing import Any

from evotrias import scheduler
from evotrias.db.agent_index import AgentInfo
from evotrias.enums_and_consts import Agents, Decisions, Tools
from evotrias.hooks import hook_manager
from evotrias.hooks.claude.common import merge_pre_tool_use_output, resolve_agent_name
from evotrias.logger import logger
from evotrias.permissions import evaluate_permission

_TOOL_NAME_MAP: dict[str, Tools] = {
    "Read": Tools.READ,
    "Write": Tools.WRITE,
    "Edit": Tools.EDIT,
    "Bash": Tools.BASH,
    "Glob": Tools.GLOB,
    "Grep": Tools.GREP,
    "Agent": Tools.AGENT,
}


def _normalize_tool_param(tool_type: Tools, tool_input: dict[str, Any]) -> dict[str, Any]:
    if tool_type == Tools.READ:
        return {"file_path": tool_input.get("file_path", "")}
    if tool_type == Tools.WRITE:
        return {"file_path": tool_input.get("file_path", ""), "content": tool_input.get("content", "")}
    if tool_type == Tools.EDIT:
        return {
            "file_path": tool_input.get("file_path", ""),
            "old_string": tool_input.get("old_string", ""),
            "new_string": tool_input.get("new_string", ""),
        }
    if tool_type == Tools.BASH:
        return {"command": tool_input.get("command", "")}
    if tool_type == Tools.GLOB:
        return {"pattern": tool_input.get("pattern", ""), "path": tool_input.get("path")}
    if tool_type == Tools.GREP:
        return {
            "pattern": tool_input.get("pattern", ""),
            "path": tool_input.get("path"),
            "glob": tool_input.get("glob"),
        }
    if tool_type == Tools.AGENT:
        return {"subagent": resolve_agent_name(tool_input.get("subagent_type"))}
    return {"raw_tool_name": str(tool_input.get("tool_name", "")), "raw_tool_input": tool_input}


@hook_manager.register("claude", "pre_tool_use", priority=0)
def claude_tool_permission_gate(payload: dict, result: dict, extra_data: Any) -> dict[str, Any] | None:
    logger.debug(f"permission gate invoked with: {payload=}, {result=}, {extra_data=}")
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    session_id = str(payload.get("session_id") or "")
    agent_id = payload.get("agent_id")

    run_id = scheduler.lookup_run_by_session(session_id)
    if run_id is None:
        logger.debug(f"no run_id found for session_id={session_id}, bypassing")
        return result

    if agent_id is None:
        agent_info = AgentInfo(agent_type=Agents.MAIN)
    else:
        agent_info = scheduler.lookup_agent(run_id, agent_id)
    if agent_info is None:
        logger.error(f"failed to find agent_info for run_id={run_id}, agent_id={agent_id}")
        return result

    tool_type = _TOOL_NAME_MAP.get(tool_name, Tools.UNKNOWN)
    tool_param = _normalize_tool_param(tool_type, tool_input)

    decision, reason = evaluate_permission(tool_type, tool_param, run_id, agent_info)
    if decision == Decisions.BYPASS:
        return result
    return merge_pre_tool_use_output(result, permissionDecision=decision.value, permissionDecisionReason=reason)

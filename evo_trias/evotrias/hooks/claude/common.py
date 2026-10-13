"""Helpers shared by the Claude hook handlers."""

from __future__ import annotations

from typing import Any

from evotrias.enums import Agents

AGENT_TYPE_MAP: dict[str, Agents] = {
    "evotrias-task-executor": Agents.TASK_EXECUTOR,
    "evotrias-init-explorer": Agents.INIT_EXPLORER,
    "evotrias-meta-guidance-generator": Agents.META_GUIDANCE_GENERATOR,
}


def resolve_agent_name(raw: Any) -> Agents:
    """Map a Claude agent name (``agent_type`` / ``subagent_type``) to ``Agents``; unrecognized -> UNKNOWN."""
    return AGENT_TYPE_MAP.get(str(raw or "").strip().lower(), Agents.UNKNOWN)


def current_tool_input(payload: dict, result: dict) -> dict[str, Any]:
    """The tool input as left by earlier handlers in the chain, else the original one."""
    updated = (result.get("hookSpecificOutput") or {}).get("updatedInput")
    if updated is not None:
        return updated
    return payload.get("tool_input") or {}


def merge_pre_tool_use_output(result: dict, **fields: Any) -> dict[str, Any]:
    """Add ``fields`` to ``hookSpecificOutput``, keeping what earlier handlers wrote.

    A ``deny`` already present is never downgraded to ``allow``.
    """
    out = dict(result.get("hookSpecificOutput") or {})
    if out.get("permissionDecision") == "deny" and fields.get("permissionDecision") == "allow":
        fields = {k: v for k, v in fields.items() if k not in ("permissionDecision", "permissionDecisionReason")}
    out["hookEventName"] = "PreToolUse"
    out.update(fields)
    return result | {"hookSpecificOutput": out}

from __future__ import annotations

from typing import Any, Callable

from evotrias.enums import Agents, Decisions, Tools
from evotrias.logger import logger
from evotrias.permissions.handler_base import Handler, DEFAULT_ALLOW_REASON


_pipelines: dict[Agents, list[type[Handler]]] = {}


def register(agent_type: Agents, priority: int = 0) -> Callable[[type[Handler]], type[Handler]]:
    def _wrap(cls: type[Handler]) -> type[Handler]:
        cls.priority = priority
        _pipelines.setdefault(agent_type, []).append(cls)
        _pipelines[agent_type].sort(key=lambda c: -c.priority)
        return cls

    return _wrap


def evaluate_permission(
    tool_type: Tools,
    tool_param: dict[str, Any],
    run_id: str,
    agent_info: dict[str, Any],
) -> tuple[Decisions, str]:
    logger.info(f'Evaluating permission for tool {tool_type.name}')
    logger.debug(f'{agent_info=!r}, {tool_param=!r}, {run_id=!r}')
    agent_type = agent_info.get("agent_type", None)
    if agent_type is None:
        logger.warning(f'Failed to solve agent type from agent_info, fall back to UNKNOWN. {agent_info=!r}')
        agent_type = Agents.UNKNOWN
    handlers = _pipelines.get(agent_type, [])
    if not handlers:
        logger.info(f'Final decision: BYPASS (no handlers for agent type {agent_type})')
        return Decisions.BYPASS, ""
    saw_allow = False
    for cls in handlers:
        if not cls.filter(tool_type, tool_param, run_id, agent_info):
            continue
        decision, reason = cls.check(tool_type, tool_param, run_id, agent_info)
        logger.debug(f"permission: {cls.rule_name} -> {decision.name} {reason!r}")
        if decision == Decisions.DENY:
            logger.info(f'Final decision: DENY, {reason} (handler: {cls.rule_name})')
            return Decisions.DENY, reason
        if decision == Decisions.ALLOW:
            saw_allow = True
    if saw_allow:
        logger.info(f'Final decision: ALLOW (at least one handler allowed)')
        return Decisions.ALLOW, DEFAULT_ALLOW_REASON
    logger.info(f'Final decision: BYPASS (no handlers allowed)')
    return Decisions.BYPASS, ""

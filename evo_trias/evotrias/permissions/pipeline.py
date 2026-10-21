# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from evotrias.db.agent_index import AgentInfo
from evotrias.enums_and_consts import Agents, Decisions, Tools
from evotrias.logger import logger
from evotrias.permissions.handler_base import DEFAULT_ALLOW_REASON, Handler

_pipelines: dict[Agents, list[type[Handler]]] = {}
_H = TypeVar("_H", bound=type[Handler])


def register(agent_type: Agents, priority: int = 0) -> Callable[[_H], _H]:
    def _wrap(cls: _H) -> _H:
        cls.priority = priority
        _pipelines.setdefault(agent_type, []).append(cls)
        _pipelines[agent_type].sort(key=lambda c: -c.priority)
        return cls

    return _wrap


def evaluate_permission(
    tool_type: Tools,
    tool_param: dict[str, Any],
    run_id: str,
    agent_info: AgentInfo,
) -> tuple[Decisions, str]:
    logger.info(f"Evaluating permission for tool {tool_type.name}")
    logger.debug(f"{agent_info=!r}, {tool_param=!r}, {run_id=!r}")
    agent_type = agent_info.agent_type
    handlers = _pipelines.get(agent_type, [])
    if not handlers:
        logger.info(f"Final decision: BYPASS (no handlers for agent type {agent_type})")
        return Decisions.BYPASS, ""
    saw_allow = False
    for cls in handlers:
        if not cls.filter(tool_type, tool_param, run_id, agent_info):
            continue
        decision, reason = cls.check(tool_type, tool_param, run_id, agent_info)
        logger.debug(f"permission: {cls.rule_name} -> {decision.name} {reason!r}")
        if decision == Decisions.DENY:
            logger.info(f"Final decision: DENY, {reason} (handler: {cls.rule_name})")
            return Decisions.DENY, reason
        if decision == Decisions.ALLOW:
            saw_allow = True
    if saw_allow:
        logger.info("Final decision: ALLOW (at least one handler allowed)")
        return Decisions.ALLOW, DEFAULT_ALLOW_REASON
    logger.info("Final decision: BYPASS (no handlers allowed)")
    return Decisions.BYPASS, ""

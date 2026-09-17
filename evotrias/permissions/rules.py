from __future__ import annotations

from typing import Any

from evotrias.db.agent_index import AgentInfo
from evotrias.enums import Agents, Decisions, Tools
from evotrias.permissions.handler_base import Handler
from evotrias.permissions.pipeline import register


@register(Agents.MAIN, priority=0)
@register(Agents.INIT_EXPLORER, priority=0)
@register(Agents.META_GUIDANCE_GENERATOR, priority=0)
@register(Agents.TASK_EXECUTOR, priority=0)
class NoEvaluatorAccess(Handler):
    rule_name = "no_evaluator_access"
    _FORBIDDEN_FILES = ("evaluator.py",)

    @staticmethod
    def filter(
        tool_type: Tools, tool_param: dict[str, Any], run_id: str, agent_info: AgentInfo
    ) -> bool:
        return tool_type in (Tools.READ, Tools.GLOB, Tools.GREP)

    @staticmethod
    def check(
        tool_type: Tools, tool_param: dict[str, Any], run_id: str, agent_info: AgentInfo
    ) -> tuple[Decisions, str]:
        targets: list[str] = []
        if tool_type == Tools.READ:
            targets = [str(tool_param.get("file_path", ""))]
        elif tool_type == Tools.GLOB:
            targets = [str(tool_param.get("path") or "")]
        elif tool_type == Tools.GREP:
            targets = [str(tool_param.get("path") or "")]
            g = tool_param.get("glob")
            if g:
                targets.append(str(g))
        for t in targets:
            low = t.lower()
            for forbidden in NoEvaluatorAccess._FORBIDDEN_FILES:
                if low.endswith(forbidden) or forbidden in low:
                    return Decisions.DENY, (
                        f"Access to evaluator script {t!r} denied: task-executors must NOT read the evaluator script."
                    )
        return Decisions.BYPASS, "No forbidden evaluator script access detected."

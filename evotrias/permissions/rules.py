from __future__ import annotations

from typing import Any

from evotrias.db import paths
from evotrias.db.agent_index import AgentInfo
from evotrias.db.config import Config
from evotrias.enums import Agents, Decisions, Tools
from evotrias.permissions.handler_base import Handler
from evotrias.permissions.pipeline import register
from evotrias.utils.path_util import resolve_path


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


@register(Agents.INIT_EXPLORER, priority=0)
class InitExplorerOnlyWritesRootDirection(Handler):
    rule_name = "init_explorer_only_writes_root_direction"

    @staticmethod
    def filter(
        tool_type: Tools, tool_param: dict[str, Any], run_id: str, agent_info: AgentInfo
    ) -> bool:
        return tool_type in (Tools.WRITE, Tools.EDIT)

    @staticmethod
    def check(
        tool_type: Tools, tool_param: dict[str, Any], run_id: str, agent_info: AgentInfo
    ) -> tuple[Decisions, str]:
        raw = str(tool_param.get("file_path", ""))
        root_number = Config(run_id).scheduler.lineage_root_number
        tasks_dir = paths.public_run_dir(run_id) / "tasks"
        allowed = {resolve_path(tasks_dir / f"task_{seq:03d}" / "direction.txt") for seq in range(1, root_number + 1)}
        if resolve_path(raw) in allowed:
            return Decisions.ALLOW, f"init-explorer writing its root direction file {raw!r}."
        return Decisions.DENY, (
            f"Write to {raw!r} denied: init-explorer may only write "
            f"{tasks_dir.as_posix()}/task_NNN/direction.txt for root tasks task_001..task_{root_number:03d}."
        )

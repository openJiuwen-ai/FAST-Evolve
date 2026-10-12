from __future__ import annotations

import re
from typing import Any

from evotrias.db import paths
from evotrias.db.agent_index import AgentInfo
from evotrias.db.attempts import Attempts
from evotrias.db.config import Config
from evotrias.db.task_contexts import TaskContexts
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
    def filter(tool_type: Tools, tool_param: dict[str, Any], run_id: str, agent_info: AgentInfo) -> bool:
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
    def filter(tool_type: Tools, tool_param: dict[str, Any], run_id: str, agent_info: AgentInfo) -> bool:
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


@register(Agents.TASK_EXECUTOR, priority=0)
class TaskExecutorOnlyWritesOwnAttempts(Handler):
    rule_name = "task_executor_only_writes_own_attempts"

    @staticmethod
    def filter(tool_type: Tools, tool_param: dict[str, Any], run_id: str, agent_info: AgentInfo) -> bool:
        return tool_type in (Tools.WRITE, Tools.EDIT)

    @staticmethod
    def check(
        tool_type: Tools, tool_param: dict[str, Any], run_id: str, agent_info: AgentInfo
    ) -> tuple[Decisions, str]:
        raw = str(tool_param.get("file_path", ""))
        task_id = agent_info.attrs.get("task_id")
        if not task_id:
            return Decisions.DENY, f"Write to {raw!r} denied: task-executor has no task bound yet."
        attempts_dir = paths.public_run_dir(run_id) / "tasks" / str(task_id) / "attempts"
        attempts_root = resolve_path(attempts_dir)
        target = resolve_path(raw)
        if target.is_relative_to(attempts_root) and target != attempts_root:
            attempt_id, *rest = target.relative_to(attempts_root).parts
            if rest and re.fullmatch(r"attempt_\d{3}", attempt_id):
                return Decisions.ALLOW, f"task-executor writing inside its own attempt directory {raw!r}."
        return Decisions.DENY, (
            f"Write to {raw!r} denied: task-executor may only write under {attempts_dir.as_posix()}/attempt_NNN/."
        )


@register(Agents.TASK_EXECUTOR, priority=0)
class TaskExecutorReadAllowlistUnderRuns(Handler):
    rule_name = "task_executor_read_allowlist_under_runs"

    @staticmethod
    def filter(tool_type: Tools, tool_param: dict[str, Any], run_id: str, agent_info: AgentInfo) -> bool:
        return tool_type in (Tools.READ, Tools.GLOB, Tools.GREP)

    @staticmethod
    def check(
        tool_type: Tools, tool_param: dict[str, Any], run_id: str, agent_info: AgentInfo
    ) -> tuple[Decisions, str]:
        raw = str(tool_param.get("file_path" if tool_type == Tools.READ else "path") or "")
        if not raw:
            return Decisions.BYPASS, "No explicit path to judge."
        target = resolve_path(raw)
        if not target.is_relative_to(resolve_path(paths.RUNS_ROOT)):
            return Decisions.BYPASS, f"{raw!r} is outside {paths.RUNS_ROOT.as_posix()}/."
        task_id = agent_info.attrs.get("task_id")
        if not task_id:
            return Decisions.DENY, f"Read of {raw!r} denied: task-executor has no task bound yet."
        task_dir = paths.public_run_dir(run_id) / "tasks" / str(task_id)
        if target.is_relative_to(resolve_path(task_dir)):
            return Decisions.ALLOW, f"task-executor reading inside its own task directory {raw!r}."
        context = TaskContexts(run_id).get(str(task_id))
        selected = [i for i in [context.parent_id, *context.inspiration_ids] if i] if context else []
        successes = Attempts(run_id).successes
        allowed_dirs = {
            resolve_path(successes[i].artifact_ref).parent
            for i in selected
            if i in successes and successes[i].artifact_ref
        }
        if target in allowed_dirs or any(target.is_relative_to(d) for d in allowed_dirs):
            return Decisions.ALLOW, f"task-executor reading a selected parent/inspiration artifact dir {raw!r}."
        return Decisions.DENY, (
            f"Read of {raw!r} denied: under {paths.RUNS_ROOT.as_posix()}/ task-executor may only read "
            f"{task_dir.as_posix()}/ and the artifact_ref directories returned by select_parent/select_inspirations."
        )


@register(Agents.INIT_EXPLORER, priority=10)
@register(Agents.META_GUIDANCE_GENERATOR, priority=10)
@register(Agents.TASK_EXECUTOR, priority=10)
class NoCrossRunAccess(Handler):
    rule_name = "no_cross_run_access"

    @staticmethod
    def filter(tool_type: Tools, tool_param: dict[str, Any], run_id: str, agent_info: AgentInfo) -> bool:
        return tool_type in (Tools.READ, Tools.WRITE, Tools.EDIT, Tools.GLOB, Tools.GREP)

    @staticmethod
    def check(
        tool_type: Tools, tool_param: dict[str, Any], run_id: str, agent_info: AgentInfo
    ) -> tuple[Decisions, str]:
        raw = str(tool_param.get("path" if tool_type in (Tools.GLOB, Tools.GREP) else "file_path") or "")
        if not raw:
            return Decisions.BYPASS, "No explicit path to judge."
        target = resolve_path(raw)
        own_run = paths.public_run_dir(run_id)
        if target.is_relative_to(resolve_path(paths.RUNS_ROOT)) and not target.is_relative_to(resolve_path(own_run)):
            return Decisions.DENY, (
                f"Access to {raw!r} denied: it is outside this run's workspace {own_run.as_posix()}/; "
                f"agents of run {run_id!r} must not touch other runs."
            )
        return Decisions.BYPASS, f"{raw!r} is not another run's file."

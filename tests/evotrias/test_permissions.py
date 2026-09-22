from __future__ import annotations

import pytest

from evotrias.db.agent_index import AgentInfo
from evotrias.db.config import Config
from evotrias.enums import Agents, Decisions, Tools
from evotrias.permissions import evaluate_permission


@pytest.fixture(autouse=True)
def cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return tmp_path


RUN = "run-X"
ROOT_NUMBER = 3


@pytest.fixture
def configured_run():
    # Deliberately not the SchedulerConfig default (4), so a rule that ignores
    # the persisted config and falls back to the default fails these tests.
    with Config(RUN) as config:
        config.scheduler.lineage_root_number = ROOT_NUMBER


# Every agent type that has NoEvaluatorAccess registered.
GUARDED_AGENTS = (
    Agents.MAIN,
    Agents.INIT_EXPLORER,
    Agents.META_GUIDANCE_GENERATOR,
    Agents.TASK_EXECUTOR,
)


def _task_info(task="task_001"):
    return AgentInfo(Agents.TASK_EXECUTOR, {"task_id": task})


def _explorer_info():
    return AgentInfo(Agents.INIT_EXPLORER)


def _write(file_path: str):
    return {"file_path": file_path, "content": "x"}


def _edit(file_path: str):
    return {"file_path": file_path, "old_string": "a", "new_string": "b"}


def test_unknown_agent_bypasses():
    # UNKNOWN is indexed like any other agent, but has no pipeline registered.
    decision, _ = evaluate_permission(
        Tools.WRITE,
        {"file_path": "anywhere/at/all.py", "content": "x"},
        RUN,
        AgentInfo(Agents.UNKNOWN, {"origin_agent_type": "general-purpose"}),
    )
    assert decision == Decisions.BYPASS


def test_no_evaluator_access_denies_read_for_all_guarded_agents():
    for agent in GUARDED_AGENTS:
        decision, reason = evaluate_permission(
            Tools.READ,
            {"file_path": "benchmarks/skills/foo/scripts/evaluator.py"},
            RUN,
            AgentInfo(agent),
        )
        assert decision == Decisions.DENY, agent
        assert "evaluator" in reason.lower()


def test_no_evaluator_access_is_case_insensitive():
    decision, _ = evaluate_permission(
        Tools.READ,
        {"file_path": "benchmarks/skills/foo/scripts/EVALUATOR.PY"},
        RUN,
        _task_info(),
    )
    assert decision == Decisions.DENY


def test_no_evaluator_access_denies_grep_glob_param():
    decision, _ = evaluate_permission(
        Tools.GREP,
        {"pattern": "secret", "path": None, "glob": "**/evaluator.py"},
        RUN,
        _task_info(),
    )
    assert decision == Decisions.DENY


def test_no_evaluator_access_denies_glob_path():
    decision, _ = evaluate_permission(
        Tools.GLOB,
        {"pattern": "*", "path": "benchmarks/skills/foo/scripts/evaluator.py"},
        RUN,
        _task_info(),
    )
    assert decision == Decisions.DENY


def test_evaluator_denied_even_inside_own_workspace():
    decision, _ = evaluate_permission(
        Tools.READ,
        {"file_path": "runs/run-X/tasks/task_001/scripts/evaluator.py"},
        RUN,
        _task_info(),
    )
    assert decision == Decisions.DENY


def test_non_evaluator_read_bypasses():
    decision, _ = evaluate_permission(
        Tools.READ,
        {"file_path": "runs/run-X/tasks/task_001/solution.py"},
        RUN,
        _task_info(),
    )
    assert decision == Decisions.BYPASS


def test_write_to_evaluator_path_is_denied_by_write_scope_not_evaluator_rule():
    # NoEvaluatorAccess only guards read-like tools (READ/GLOB/GREP); the write
    # is refused by the executor's write scope, so the reason names that scope.
    decision, reason = evaluate_permission(
        Tools.WRITE,
        {"file_path": "benchmarks/skills/foo/scripts/evaluator.py", "content": "x"},
        RUN,
        _task_info(),
    )
    assert decision == Decisions.DENY
    assert "task-executor may only write" in reason


def test_unknown_tool_bypasses():
    decision, _ = evaluate_permission(Tools.UNKNOWN, {"raw_tool_name": "x"}, RUN, _task_info())
    assert decision == Decisions.BYPASS


def test_bash_is_bypass_for_task_executor():
    decision, _ = evaluate_permission(Tools.BASH, {"command": "ls"}, RUN, _task_info())
    assert decision == Decisions.BYPASS


# --- init_explorer_only_writes_root_direction -------------------------------


def test_init_explorer_allows_root_direction_txt_relative(configured_run):
    decision, _ = evaluate_permission(
        Tools.WRITE, _write("runs/run-X/tasks/task_001/direction.txt"), RUN, _explorer_info()
    )
    assert decision == Decisions.ALLOW


def test_init_explorer_allows_root_direction_txt_absolute(configured_run, cwd):
    # Claude Code hands hooks absolute paths; they must resolve to the same file.
    absolute = str(cwd / "runs" / "run-X" / "tasks" / f"task_{ROOT_NUMBER:03d}" / "direction.txt")
    decision, _ = evaluate_permission(Tools.WRITE, _write(absolute), RUN, _explorer_info())
    assert decision == Decisions.ALLOW


def test_init_explorer_denies_direction_txt_beyond_root_number(configured_run):
    beyond = f"runs/run-X/tasks/task_{ROOT_NUMBER + 1:03d}/direction.txt"
    decision, reason = evaluate_permission(Tools.WRITE, _write(beyond), RUN, _explorer_info())
    assert decision == Decisions.DENY
    assert f"task_{ROOT_NUMBER:03d}" in reason


def test_init_explorer_denies_other_file_in_root_task_dir(configured_run):
    decision, _ = evaluate_permission(
        Tools.WRITE, _write("runs/run-X/tasks/task_001/solution.py"), RUN, _explorer_info()
    )
    assert decision == Decisions.DENY


def test_init_explorer_denies_direction_txt_in_nested_dir(configured_run):
    decision, _ = evaluate_permission(
        Tools.WRITE, _write("runs/run-X/tasks/task_001/attempts/attempt_001/direction.txt"), RUN, _explorer_info()
    )
    assert decision == Decisions.DENY


def test_init_explorer_denies_path_traversal_out_of_task_dir(configured_run):
    decision, _ = evaluate_permission(
        Tools.WRITE, _write("runs/run-X/tasks/task_001/../../../evaluator.py"), RUN, _explorer_info()
    )
    assert decision == Decisions.DENY


def test_init_explorer_denies_write_outside_runs(configured_run):
    decision, _ = evaluate_permission(Tools.WRITE, _write("benchmarks/foo/solution.py"), RUN, _explorer_info())
    assert decision == Decisions.DENY


def test_init_explorer_denies_direction_txt_of_other_run(configured_run):
    decision, _ = evaluate_permission(
        Tools.WRITE, _write("runs/run-Y/tasks/task_001/direction.txt"), RUN, _explorer_info()
    )
    assert decision == Decisions.DENY


def test_init_explorer_edit_is_scoped_like_write(configured_run):
    allowed, _ = evaluate_permission(
        Tools.EDIT, _edit("runs/run-X/tasks/task_001/direction.txt"), RUN, _explorer_info()
    )
    denied, _ = evaluate_permission(Tools.EDIT, _edit("runs/run-X/tasks/task_001/notes.md"), RUN, _explorer_info())
    assert allowed == Decisions.ALLOW
    assert denied == Decisions.DENY


def test_init_explorer_denies_empty_file_path(configured_run):
    decision, _ = evaluate_permission(Tools.WRITE, _write(""), RUN, _explorer_info())
    assert decision == Decisions.DENY


def test_init_explorer_bash_and_read_are_not_write_scoped(configured_run):
    bash, _ = evaluate_permission(Tools.BASH, {"command": "mkdir -p runs/run-X/tasks/task_001"}, RUN, _explorer_info())
    read, _ = evaluate_permission(Tools.READ, {"file_path": "benchmarks/foo/SKILL.md"}, RUN, _explorer_info())
    assert bash == Decisions.BYPASS
    assert read == Decisions.BYPASS


def test_init_explorer_write_scope_does_not_apply_to_task_executor(configured_run):
    # The rule is registered for INIT_EXPLORER only; the executor keeps its own scope.
    decision, _ = evaluate_permission(
        Tools.WRITE, _write("runs/run-X/tasks/task_001/direction.txt"), RUN, _task_info()
    )
    assert decision != Decisions.ALLOW


# --- task_executor_only_writes_own_attempts ---------------------------------

OWN_ATTEMPT = "runs/run-X/tasks/task_001/attempts/attempt_001"


def test_task_executor_allows_write_in_own_attempt_dir_relative():
    decision, _ = evaluate_permission(Tools.WRITE, _write(f"{OWN_ATTEMPT}/solution.py"), RUN, _task_info())
    assert decision == Decisions.ALLOW


def test_task_executor_allows_write_in_own_attempt_dir_absolute(cwd):
    absolute = str(cwd / "runs" / "run-X" / "tasks" / "task_001" / "attempts" / "attempt_010" / "eval_result.json")
    decision, _ = evaluate_permission(Tools.WRITE, _write(absolute), RUN, _task_info())
    assert decision == Decisions.ALLOW


def test_task_executor_allows_nested_path_inside_attempt_dir():
    decision, _ = evaluate_permission(Tools.WRITE, _write(f"{OWN_ATTEMPT}/src/pkg/module.py"), RUN, _task_info())
    assert decision == Decisions.ALLOW


def test_task_executor_denies_file_directly_under_attempts():
    decision, _ = evaluate_permission(
        Tools.WRITE, _write("runs/run-X/tasks/task_001/attempts/solution.py"), RUN, _task_info()
    )
    assert decision == Decisions.DENY


def test_task_executor_denies_attempt_dir_itself_as_file():
    decision, _ = evaluate_permission(Tools.WRITE, _write(OWN_ATTEMPT), RUN, _task_info())
    assert decision == Decisions.DENY


@pytest.mark.parametrize("bad", ["attempt_1", "attempt_0001", "attempt-001", "attempts_001", "final", "attempt_00a"])
def test_task_executor_denies_malformed_attempt_id(bad):
    decision, _ = evaluate_permission(
        Tools.WRITE, _write(f"runs/run-X/tasks/task_001/attempts/{bad}/solution.py"), RUN, _task_info()
    )
    assert decision == Decisions.DENY


def test_task_executor_denies_own_direction_txt():
    decision, _ = evaluate_permission(
        Tools.WRITE, _write("runs/run-X/tasks/task_001/direction.txt"), RUN, _task_info()
    )
    assert decision == Decisions.DENY


def test_task_executor_denies_other_tasks_attempt_dir():
    decision, reason = evaluate_permission(
        Tools.WRITE, _write("runs/run-X/tasks/task_002/attempts/attempt_001/solution.py"), RUN, _task_info("task_001")
    )
    assert decision == Decisions.DENY
    assert "task_001/attempts" in reason


def test_task_executor_denies_attempt_dir_of_other_run():
    decision, _ = evaluate_permission(
        Tools.WRITE, _write("runs/run-Y/tasks/task_001/attempts/attempt_001/solution.py"), RUN, _task_info()
    )
    assert decision == Decisions.DENY


def test_task_executor_denies_write_outside_runs():
    decision, _ = evaluate_permission(Tools.WRITE, _write("benchmarks/foo/solution.py"), RUN, _task_info())
    assert decision == Decisions.DENY


def test_task_executor_denies_path_traversal_out_of_attempt_dir():
    decision, _ = evaluate_permission(Tools.WRITE, _write(f"{OWN_ATTEMPT}/../../direction.txt"), RUN, _task_info())
    assert decision == Decisions.DENY


def test_task_executor_edit_is_scoped_like_write():
    allowed, _ = evaluate_permission(Tools.EDIT, _edit(f"{OWN_ATTEMPT}/solution.py"), RUN, _task_info())
    denied, _ = evaluate_permission(Tools.EDIT, _edit("runs/run-X/tasks/task_001/direction.txt"), RUN, _task_info())
    assert allowed == Decisions.ALLOW
    assert denied == Decisions.DENY


@pytest.mark.parametrize("attrs", [{"task_id": None}, {}])
def test_task_executor_denies_write_when_task_not_bound(attrs):
    # SubagentStart can index the executor before executor_bind_agent backfills task_id.
    decision, reason = evaluate_permission(
        Tools.WRITE, _write(f"{OWN_ATTEMPT}/solution.py"), RUN, AgentInfo(Agents.TASK_EXECUTOR, attrs)
    )
    assert decision == Decisions.DENY
    assert "no task bound" in reason


def test_task_executor_bash_and_read_are_not_write_scoped():
    bash, _ = evaluate_permission(Tools.BASH, {"command": f"mkdir -p {OWN_ATTEMPT}"}, RUN, _task_info())
    read, _ = evaluate_permission(Tools.READ, {"file_path": "benchmarks/foo/SKILL.md"}, RUN, _task_info())
    assert bash == Decisions.BYPASS
    assert read == Decisions.BYPASS


def test_task_executor_write_scope_does_not_apply_to_init_explorer(configured_run):
    decision, _ = evaluate_permission(Tools.WRITE, _write(f"{OWN_ATTEMPT}/solution.py"), RUN, _explorer_info())
    assert decision == Decisions.DENY

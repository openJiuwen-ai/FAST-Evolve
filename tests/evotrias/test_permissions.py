from __future__ import annotations

import pytest

from evotrias.db.agent_index import AgentInfo
from evotrias.enums import Agents, Decisions, Tools
from evotrias.permissions import evaluate_permission


@pytest.fixture(autouse=True)
def cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return tmp_path


RUN = "run-X"

# Every agent type that has NoEvaluatorAccess registered.
GUARDED_AGENTS = (
    Agents.MAIN,
    Agents.INIT_EXPLORER,
    Agents.META_GUIDANCE_GENERATOR,
    Agents.TASK_EXECUTOR,
)


def _task_info(task="task_001"):
    return AgentInfo(Agents.TASK_EXECUTOR, {"task_id": task})


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


def test_write_bypasses_even_for_evaluator_path():
    # The rule only guards read-like tools (READ/GLOB/GREP); writes pass through.
    decision, _ = evaluate_permission(
        Tools.WRITE,
        {"file_path": "benchmarks/skills/foo/scripts/evaluator.py", "content": "x"},
        RUN,
        _task_info(),
    )
    assert decision == Decisions.BYPASS


def test_unknown_tool_bypasses():
    decision, _ = evaluate_permission(Tools.UNKNOWN, {"raw_tool_name": "x"}, RUN, _task_info())
    assert decision == Decisions.BYPASS


def test_bash_is_bypass_for_task_executor():
    decision, _ = evaluate_permission(Tools.BASH, {"command": "ls"}, RUN, _task_info())
    assert decision == Decisions.BYPASS

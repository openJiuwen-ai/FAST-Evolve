from __future__ import annotations

import pytest

from evotrias.db.agent_index import AgentInfo
from evotrias.db.attempts import AttemptRecord, Attempts
from evotrias.db.config import Config
from evotrias.db.task_contexts import TaskContexts
from evotrias.enums_and_consts import Agents, Decisions, Tools
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


def test_non_evaluator_read_outside_runs_bypasses():
    decision, _ = evaluate_permission(
        Tools.READ,
        {"file_path": "benchmarks/skills/foo/scripts/solution.py"},
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
    decision, _ = evaluate_permission(Tools.WRITE, _write("runs/run-X/tasks/task_001/direction.txt"), RUN, _task_info())
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
    decision, _ = evaluate_permission(Tools.WRITE, _write("runs/run-X/tasks/task_001/direction.txt"), RUN, _task_info())
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


# --- task_executor_read_allowlist_under_runs --------------------------------


def _artifact(task: str) -> str:
    return f"runs/{RUN}/tasks/{task}/attempts/attempt_001/solution.py"


def _record_success(task: str, artifact_ref: str | None = None) -> None:
    Attempts(RUN).append(
        AttemptRecord(
            id="attempt_001",
            run_id=RUN,
            task_id=task,
            subagent_id="agent",
            artifact_ref=_artifact(task) if artifact_ref is None else artifact_ref,
            parent_id=None,
            inspiration_ids=[],
            generation=0,
            verification_result={"status": "success", "fitness": 1.0},
            in_success_pool=True,
        )
    )


def _select_for(task: str, parent: str, inspirations: list[str]) -> None:
    with TaskContexts(RUN) as contexts:
        context = contexts.get(task, create=True)
        context.parent_id = parent
        context.inspiration_ids = inspirations
        context.parent_selected = True
        context.inspirations_selected = True


EXECUTOR = "task_005"


@pytest.fixture
def selected_context():
    # task_001..task_003 all succeeded; task_005 was handed task_001 as parent
    # and task_002 as its only inspiration, so task_003 is a success it must
    # not see.
    for task in ("task_001", "task_002", "task_003"):
        _record_success(task)
    _select_for(EXECUTOR, parent="task_001", inspirations=["task_002"])


def _read(file_path: str):
    return {"file_path": file_path}


def test_task_executor_allows_read_of_parent_artifact(selected_context):
    decision, _ = evaluate_permission(Tools.READ, _read(_artifact("task_001")), RUN, _task_info(EXECUTOR))
    assert decision == Decisions.ALLOW


def test_task_executor_allows_read_of_inspiration_artifact(selected_context):
    decision, _ = evaluate_permission(Tools.READ, _read(_artifact("task_002")), RUN, _task_info(EXECUTOR))
    assert decision == Decisions.ALLOW


def test_task_executor_allows_read_of_selected_artifact_by_absolute_path(selected_context, cwd):
    decision, _ = evaluate_permission(Tools.READ, _read(str(cwd / _artifact("task_001"))), RUN, _task_info(EXECUTOR))
    assert decision == Decisions.ALLOW


def test_task_executor_allows_read_when_artifact_ref_was_recorded_absolute(cwd):
    # record_attempt stores artifact_ref verbatim; executors may have passed an absolute path.
    _record_success("task_001", artifact_ref=str(cwd / _artifact("task_001")))
    _select_for(EXECUTOR, parent="task_001", inspirations=[])
    decision, _ = evaluate_permission(Tools.READ, _read(_artifact("task_001")), RUN, _task_info(EXECUTOR))
    assert decision == Decisions.ALLOW


def test_task_executor_denies_read_of_unselected_success_artifact(selected_context):
    decision, reason = evaluate_permission(Tools.READ, _read(_artifact("task_003")), RUN, _task_info(EXECUTOR))
    assert decision == Decisions.DENY
    assert "select_parent/select_inspirations" in reason


@pytest.mark.parametrize(
    "sibling",
    [
        f"runs/{RUN}/tasks/task_001/attempts/attempt_001/eval_result.json",
        f"runs/{RUN}/tasks/task_001/attempts/attempt_001",
        f"runs/{RUN}/tasks/task_001/attempts/attempt_001/src/helper.py",
    ],
)
def test_task_executor_allows_read_of_files_in_selected_artifact_dir(selected_context, sibling):
    # The whole directory holding a selected parent/inspiration artifact is readable,
    # not just the artifact_ref file itself.
    decision, _ = evaluate_permission(Tools.READ, _read(sibling), RUN, _task_info(EXECUTOR))
    assert decision == Decisions.ALLOW


def test_task_executor_denies_read_of_parent_direction_txt(selected_context):
    # direction.txt sits in the task dir, outside the artifact's attempt directory.
    decision, _ = evaluate_permission(
        Tools.READ, _read(f"runs/{RUN}/tasks/task_001/direction.txt"), RUN, _task_info(EXECUTOR)
    )
    assert decision == Decisions.DENY


def test_task_executor_denies_read_of_parent_attempt_dir_other_than_selected(selected_context):
    # The parent's own other attempt directory (attempt_002) was not the selected artifact.
    decision, _ = evaluate_permission(
        Tools.READ, _read(f"runs/{RUN}/tasks/task_001/attempts/attempt_002/solution.py"), RUN, _task_info(EXECUTOR)
    )
    assert decision == Decisions.DENY


@pytest.mark.parametrize(
    "own",
    [
        f"runs/{RUN}/tasks/{EXECUTOR}/direction.txt",
        f"runs/{RUN}/tasks/{EXECUTOR}/attempts/attempt_001/eval_result.json",
        f"runs/{RUN}/tasks/{EXECUTOR}/attempts/attempt_002/solution.py",
        f"runs/{RUN}/tasks/{EXECUTOR}",
    ],
)
def test_task_executor_allows_read_inside_own_task_dir(selected_context, own):
    decision, _ = evaluate_permission(Tools.READ, _read(own), RUN, _task_info(EXECUTOR))
    assert decision == Decisions.ALLOW


def test_task_executor_denies_read_under_other_run(selected_context):
    decision, _ = evaluate_permission(
        Tools.READ, _read("runs/run-Y/tasks/task_005/direction.txt"), RUN, _task_info(EXECUTOR)
    )
    assert decision == Decisions.DENY


def test_task_executor_denies_read_traversal_out_of_own_task_dir(selected_context):
    traversal = f"runs/{RUN}/tasks/{EXECUTOR}/../task_003/attempts/attempt_001/solution.py"
    decision, _ = evaluate_permission(Tools.READ, _read(traversal), RUN, _task_info(EXECUTOR))
    assert decision == Decisions.DENY


def test_task_executor_reads_under_runs_before_selection_only_own_task_dir():
    # No task_contexts entry yet (select_parent not called): nothing but the own dir is readable.
    _record_success("task_001")
    own_direction = f"runs/{RUN}/tasks/{EXECUTOR}/direction.txt"
    own, _ = evaluate_permission(Tools.READ, _read(own_direction), RUN, _task_info(EXECUTOR))
    other, _ = evaluate_permission(Tools.READ, _read(_artifact("task_001")), RUN, _task_info(EXECUTOR))
    assert own == Decisions.ALLOW
    assert other == Decisions.DENY


def test_task_executor_glob_and_grep_are_judged_by_path(selected_context):
    own_glob, _ = evaluate_permission(
        Tools.GLOB, {"pattern": "*.json", "path": f"runs/{RUN}/tasks/{EXECUTOR}/attempts"}, RUN, _task_info(EXECUTOR)
    )
    foreign_glob, _ = evaluate_permission(
        Tools.GLOB, {"pattern": "*.py", "path": f"runs/{RUN}/tasks"}, RUN, _task_info(EXECUTOR)
    )
    parent_grep, _ = evaluate_permission(
        Tools.GREP, {"pattern": "def", "path": _artifact("task_001"), "glob": None}, RUN, _task_info(EXECUTOR)
    )
    foreign_grep, _ = evaluate_permission(
        Tools.GREP, {"pattern": "def", "path": "runs", "glob": None}, RUN, _task_info(EXECUTOR)
    )
    assert own_glob == Decisions.ALLOW
    assert foreign_glob == Decisions.DENY
    assert parent_grep == Decisions.ALLOW
    assert foreign_grep == Decisions.DENY


def test_task_executor_glob_and_grep_without_path_bypass(selected_context):
    glob, _ = evaluate_permission(Tools.GLOB, {"pattern": "**/*.py", "path": None}, RUN, _task_info(EXECUTOR))
    grep, _ = evaluate_permission(Tools.GREP, {"pattern": "def", "path": None, "glob": None}, RUN, _task_info(EXECUTOR))
    assert glob == Decisions.BYPASS
    assert grep == Decisions.BYPASS


def test_task_executor_read_under_runs_denied_when_task_not_bound(selected_context):
    unbound = AgentInfo(Agents.TASK_EXECUTOR, {})
    under_runs, reason = evaluate_permission(Tools.READ, _read(_artifact("task_001")), RUN, unbound)
    outside, _ = evaluate_permission(Tools.READ, _read("benchmarks/foo/SKILL.md"), RUN, unbound)
    assert under_runs == Decisions.DENY
    assert "no task bound" in reason
    assert outside == Decisions.BYPASS


def test_task_executor_read_outside_runs_bypasses(selected_context):
    decision, _ = evaluate_permission(Tools.READ, _read("benchmarks/foo/SKILL.md"), RUN, _task_info(EXECUTOR))
    assert decision == Decisions.BYPASS


def test_task_executor_evaluator_inside_own_task_dir_still_denied(selected_context):
    # The allowlist ALLOWs the own dir, but NoEvaluatorAccess's DENY wins in the pipeline.
    decision, reason = evaluate_permission(
        Tools.READ, _read(f"runs/{RUN}/tasks/{EXECUTOR}/attempts/attempt_001/evaluator.py"), RUN, _task_info(EXECUTOR)
    )
    assert decision == Decisions.DENY
    assert "evaluator script" in reason


def test_read_allowlist_does_not_apply_to_other_agents(selected_context):
    for info in (_explorer_info(), AgentInfo(Agents.META_GUIDANCE_GENERATOR)):
        decision, _ = evaluate_permission(Tools.READ, _read(_artifact("task_003")), RUN, info)
        assert decision == Decisions.BYPASS, info.agent_type


# --- no_cross_run_access ----------------------------------------------------

ISOLATED_AGENTS = (Agents.INIT_EXPLORER, Agents.META_GUIDANCE_GENERATOR, Agents.TASK_EXECUTOR)
PATH_TOOLS = (Tools.READ, Tools.WRITE, Tools.EDIT, Tools.GLOB, Tools.GREP)
OTHER_RUN_FILE = "runs/run-Y/tasks/task_001/attempts/attempt_001/solution.py"


def _info(agent: Agents) -> AgentInfo:
    return _task_info() if agent == Agents.TASK_EXECUTOR else AgentInfo(agent)


def _param(tool: Tools, path: str) -> dict:
    if tool == Tools.GLOB:
        return {"pattern": "*", "path": path}
    if tool == Tools.GREP:
        return {"pattern": "x", "path": path, "glob": None}
    if tool == Tools.WRITE:
        return _write(path)
    if tool == Tools.EDIT:
        return _edit(path)
    return _read(path)


@pytest.mark.parametrize("agent", ISOLATED_AGENTS)
@pytest.mark.parametrize("tool", PATH_TOOLS)
def test_cross_run_access_denied_for_every_isolated_agent_and_path_tool(agent, tool):
    decision, reason = evaluate_permission(tool, _param(tool, OTHER_RUN_FILE), RUN, _info(agent))
    assert decision == Decisions.DENY
    assert "other runs" in reason and RUN in reason


@pytest.mark.parametrize(
    "path",
    [
        "runs/run-Y/tasks/task_001/direction.txt",
        "runs/run-X/../run-Y/tasks/task_001/direction.txt",
        "runs/run-XY/tasks/task_001/direction.txt",
        "runs",
        "runs/stray.txt",
    ],
)
def test_cross_run_access_denied_paths(path):
    decision, _ = evaluate_permission(Tools.READ, _read(path), RUN, AgentInfo(Agents.META_GUIDANCE_GENERATOR))
    assert decision == Decisions.DENY


def test_cross_run_access_denied_by_absolute_path(cwd):
    absolute = str(cwd / "runs" / "run-Y" / "tasks" / "task_001" / "direction.txt")
    decision, _ = evaluate_permission(Tools.READ, _read(absolute), RUN, AgentInfo(Agents.META_GUIDANCE_GENERATOR))
    assert decision == Decisions.DENY


@pytest.mark.parametrize("agent", [Agents.INIT_EXPLORER, Agents.META_GUIDANCE_GENERATOR])
def test_own_run_read_is_not_decided_by_cross_run_rule(agent):
    # Neither agent has a read rule under runs/, so the pipeline answer is this rule's alone: no opinion.
    decision, _ = evaluate_permission(Tools.READ, _read(_artifact("task_001")), RUN, AgentInfo(agent))
    assert decision == Decisions.BYPASS


def test_cross_run_rule_ignores_paths_outside_runs():
    decision, _ = evaluate_permission(
        Tools.WRITE, _write("benchmarks/foo/x.py"), RUN, AgentInfo(Agents.META_GUIDANCE_GENERATOR)
    )
    assert decision == Decisions.BYPASS


def test_cross_run_rule_not_registered_for_main():
    decision, _ = evaluate_permission(Tools.READ, _read(OTHER_RUN_FILE), RUN, AgentInfo(Agents.MAIN))
    assert decision == Decisions.BYPASS


@pytest.mark.parametrize(
    ("tool", "scope_reason"),
    [(Tools.WRITE, "may only write"), (Tools.READ, "may only read")],
)
def test_cross_run_reason_outranks_task_executor_scope_rules(tool, scope_reason):
    decision, reason = evaluate_permission(tool, _param(tool, OTHER_RUN_FILE), RUN, _task_info())
    assert decision == Decisions.DENY
    assert "other runs" in reason
    assert scope_reason not in reason


def test_cross_run_rule_bypasses_bash_and_empty_paths():
    info = AgentInfo(Agents.META_GUIDANCE_GENERATOR)
    bash, _ = evaluate_permission(Tools.BASH, {"command": "cat runs/run-Y/x"}, RUN, info)
    glob, _ = evaluate_permission(Tools.GLOB, {"pattern": "*", "path": None}, RUN, info)
    read, _ = evaluate_permission(Tools.READ, _read(""), RUN, info)
    assert bash == Decisions.BYPASS
    assert glob == Decisions.BYPASS
    assert read == Decisions.BYPASS


@pytest.mark.parametrize("subagent", [Agents.INIT_EXPLORER, Agents.TASK_EXECUTOR, Agents.META_GUIDANCE_GENERATOR])
def test_main_may_dispatch_known_agents(subagent):
    decision, _ = evaluate_permission(Tools.AGENT, {"subagent": subagent}, RUN, AgentInfo(Agents.MAIN))
    assert decision == Decisions.ALLOW


def test_main_dispatch_of_unknown_agent_bypasses():
    decision, _ = evaluate_permission(Tools.AGENT, {"subagent": Agents.UNKNOWN}, RUN, AgentInfo(Agents.MAIN))
    assert decision == Decisions.BYPASS


def test_agent_dispatch_rule_not_registered_for_subagents():
    decision, _ = evaluate_permission(Tools.AGENT, {"subagent": Agents.TASK_EXECUTOR}, RUN, _task_info())
    assert decision == Decisions.BYPASS

from __future__ import annotations

import json

from agent_evolve_mcp.controller import EvolutionController
from agent_evolve_mcp.storage import attempts_log_path, read_jsonl
from agent_evolve_mcp.models import (
    ConvergenceConfig,
    EvolutionControllerConfig,
    MetaEvolveConfig,
    SuccessRecord,
)


def _controller(tmp_path, monkeypatch, *, max_total_tasks=100, watch_tasks=0):
    monkeypatch.setenv("AGENT_EVOLVE_CONTROLLER_DIR", str(tmp_path / "controller"))
    config = EvolutionControllerConfig(
        convergence=ConvergenceConfig(
            enabled=True,
            convergence_window=1,
            perf_improvement_threshold=0.1,
            activity_threshold=0.0,
            patience=1,
            max_total_tasks=max_total_tasks,
        ),
        meta_evolve=MetaEvolveConfig(
            enabled=True,
            watch_tasks_before_guidance=watch_tasks,
            guidance_num_to_generate=3,
            guidance_max_uses=2,
            max_tried_guidance=10,
        ),
    )
    controller = EvolutionController("run_meta_test", config=config)
    controller.configure_run("optimize the test task")
    return controller


def _add_successes(controller: EvolutionController, *, fitness=1.0, count=5):
    for index in range(count):
        controller.db.add(
            SuccessRecord(
                id=f"success_{index:04d}",
                run_id=controller.run_id,
                fitness=fitness,
                artifact_ref=f"artifact_{index}.py",
                created_at=f"2026-01-01T00:00:0{index}+00:00",
            )
        )
    controller._load_state()


def test_plateau_requests_guidance_without_stop(tmp_path, monkeypatch):
    controller = _controller(tmp_path, monkeypatch)
    _add_successes(controller)

    result = controller.check_convergence()

    assert result["action"] == "request_meta_guidance"
    assert result["should_stop"] is False
    assert result["search_state"] == "watching"
    assert result["request_id"].startswith("mgr_")


def test_hard_budget_still_stops(tmp_path, monkeypatch):
    controller = _controller(tmp_path, monkeypatch, max_total_tasks=1)
    controller.task_contexts.ensure("task_001")

    result = controller.check_convergence()

    assert result["action"] == "budget_exhausted"
    assert result["should_stop"] is True
    assert result["search_state"] == "stopped"


def test_commit_guidance_uses_canonical_best_anchor(tmp_path, monkeypatch):
    controller = _controller(tmp_path, monkeypatch)
    _add_successes(controller, fitness=1.0)

    convergence = controller.check_convergence()
    request = controller.get_meta_guidance_request(convergence["request_id"])
    commit = controller.commit_meta_guidance(
        request["request_id"],
        {
            "guidance": [
                {
                    "idea": "Use a decomposition-first implementation",
                    "description": "Try a structural decomposition before local tuning.",
                    "target_metric": "fitness",
                    "novelty_claim": "Existing records have equal fitness and share the same direct path.",
                    "implementation_plan": ["Create decomposition helper", "Keep parent fallback"],
                    "cautions": ["Keep output format unchanged"],
                    "approach_type": "decomposition",
                    "avoid_repeating": [],
                    "max_uses": 2,
                }
            ]
        },
    )

    parent = controller.select_parent("task_100")

    assert commit["committed"] is True
    assert commit["anchor_parent_id"] == "success_0000"
    assert parent["parent_id"] == "success_0000"
    assert parent["artifact_ref"] == "artifact_0.py"


def test_commit_guidance_accepts_json_string_and_rejects_bad_max_uses(tmp_path, monkeypatch):
    controller = _controller(tmp_path, monkeypatch)
    _add_successes(controller, fitness=1.0)
    convergence = controller.check_convergence()
    request = controller.get_meta_guidance_request(convergence["request_id"])

    bad = controller.commit_meta_guidance(
        request["request_id"],
        {
            "guidance": [
                {
                    "idea": "Try a different representation",
                    "description": "Represent the solution differently before optimization.",
                    "novelty_claim": "Current attempts reuse the same representation.",
                    "implementation_plan": ["Change representation"],
                    "approach_type": "representation",
                    "max_uses": "two",
                }
            ]
        },
    )
    good = controller.commit_meta_guidance(
        request["request_id"],
        json.dumps(
            {
                "guidance": [
                    {
                        "idea": "Try a different representation",
                        "description": "Represent the solution differently before optimization.",
                        "novelty_claim": "Current attempts reuse the same representation.",
                        "implementation_plan": ["Change representation"],
                        "approach_type": "representation",
                        "max_uses": "2",
                    }
                ]
            }
        ),
    )

    assert bad == {"committed": False, "reason": "invalid_max_uses"}
    assert good["committed"] is True


def test_same_task_does_not_double_consume_guidance(tmp_path, monkeypatch):
    controller = _controller(tmp_path, monkeypatch)
    _add_successes(controller, fitness=1.0)
    convergence = controller.check_convergence()
    request = controller.get_meta_guidance_request(convergence["request_id"])
    controller.commit_meta_guidance(
        request["request_id"],
        {
            "guidance": [
                {
                    "idea": "Try a different representation",
                    "description": "Represent the solution differently before optimization.",
                    "novelty_claim": "Current attempts reuse the same representation.",
                    "implementation_plan": ["Change representation", "Adapt output conversion"],
                    "approach_type": "representation",
                    "max_uses": 2,
                }
            ]
        },
    )

    first = controller.select_parent("task_101")
    second = controller.select_parent("task_101")
    controller.meta_guidance.load()
    active = controller.meta_guidance.state["active_guidance"][0]

    assert first == second
    assert active["uses"] == 1


def test_no_new_request_while_assigned_guidance_is_unresolved(tmp_path, monkeypatch):
    controller = _controller(tmp_path, monkeypatch)
    _add_successes(controller, fitness=1.0)
    convergence = controller.check_convergence()
    request = controller.get_meta_guidance_request(convergence["request_id"])
    controller.commit_meta_guidance(
        request["request_id"],
        {
            "guidance": [
                {
                    "idea": "Try a bounded alternative search",
                    "description": "Use a different local search family before returning to baseline.",
                    "novelty_claim": "Recent attempts are not changing the local search family.",
                    "implementation_plan": ["Implement bounded alternative", "Compare to parent"],
                    "approach_type": "local_search",
                    "max_uses": 2,
                }
            ]
        },
    )

    controller.select_parent("task_201")
    controller.select_parent("task_202")
    controller.meta_guidance.load()
    assert controller.meta_guidance.state["active_guidance"][0]["uses"] == 2

    followup = controller.check_convergence()

    assert followup["action"] == "continue"
    assert followup["meta_guidance"]["active_count"] == 1


def test_pending_request_is_re_emitted_until_committed(tmp_path, monkeypatch):
    controller = _controller(tmp_path, monkeypatch)
    _add_successes(controller, fitness=1.0)

    first = controller.check_convergence()
    second = controller.check_convergence()

    assert first["action"] == "request_meta_guidance"
    assert second["action"] == "request_meta_guidance"
    assert second["request_id"] == first["request_id"]


def test_pending_request_is_cleared_after_new_best(tmp_path, monkeypatch):
    controller = _controller(tmp_path, monkeypatch)
    _add_successes(controller, fitness=1.0)
    first = controller.check_convergence()
    assert first["action"] == "request_meta_guidance"

    controller.db.add(
        SuccessRecord(
            id="success_new_best",
            run_id=controller.run_id,
            fitness=2.0,
            artifact_ref="new_best.py",
            created_at="2026-01-01T00:01:00+00:00",
        )
    )
    controller._load_state()
    after_improvement = controller.check_convergence()
    request = controller.get_meta_guidance_request(first["request_id"])

    assert after_improvement["action"] == "continue"
    assert after_improvement["search_state"] == "exploring"
    assert request["available"] is False


def test_attempt_records_meta_guidance_and_report_summary(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    controller = _controller(tmp_path, monkeypatch)
    _add_successes(controller, fitness=1.0)
    convergence = controller.check_convergence()
    request = controller.get_meta_guidance_request(convergence["request_id"])
    controller.commit_meta_guidance(
        request["request_id"],
        {
            "guidance": [
                {
                    "idea": "Switch representation",
                    "description": "Change representation before applying the existing objective.",
                    "target_metric": "fitness",
                    "novelty_claim": "The current bests use the same representation.",
                    "implementation_plan": ["Build alternate representation", "Convert output back"],
                    "cautions": ["Keep output contract unchanged"],
                    "approach_type": "representation",
                    "max_uses": 1,
                }
            ]
        },
    )
    parent = controller.select_parent("task_301")
    assert parent["parent_id"] == "success_0000"

    result_path = tmp_path / "eval_result.json"
    result_path.write_text(
        json.dumps(
            {
                "status": "success",
                "artifact_ref": "runs/run_meta_test/tasks/task_301/final.py",
                "fitness": 2.0,
                "summary": "new best via representation switch",
            }
        ),
        encoding="utf-8",
    )
    record = controller.record_attempt(
        task_id="task_301",
        verification_result_ref=str(result_path),
        attempt_id="attempt_001",
        max_attempts=3,
    )
    attempts = read_jsonl(attempts_log_path(controller.run_id))
    controller.meta_guidance.load()

    assert record["entered_success_db"] is True
    assert attempts[-1]["meta_guidance_id"] == "mg_0001"
    assert attempts[-1]["anchor_parent_id"] == "success_0000"
    assert controller.meta_guidance.state["tried_guidance"][0]["outcome"] == "new_best"

    report = controller.finalize_run()
    summary_path = tmp_path / report["summary_ref"]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert summary["meta_evolve"]["new_best_from_guidance"] == 1
    assert summary["attempts"][-1]["meta_guidance_id"] == "mg_0001"

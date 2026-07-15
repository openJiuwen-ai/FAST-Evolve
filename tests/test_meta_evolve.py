from __future__ import annotations

import json

from agent_evolve_mcp.controller import EvolutionController
from agent_evolve_mcp.storage import attempts_log_path, notes_path, read_jsonl
from agent_evolve_mcp.models import (
    ConvergenceConfig,
    EvolutionControllerConfig,
    MetaEvolveConfig,
    SuccessRecord,
)


def _controller(
    tmp_path,
    monkeypatch,
    *,
    max_total_tasks=100,
    watch_tasks=0,
    perf_improvement_threshold=0.1,
    patience=1,
):
    monkeypatch.setenv("AGENT_EVOLVE_CONTROLLER_DIR", str(tmp_path / "controller"))
    config = EvolutionControllerConfig(
        convergence=ConvergenceConfig(
            enabled=True,
            convergence_window=1,
            perf_improvement_threshold=perf_improvement_threshold,
            activity_threshold=0.0,
            patience=patience,
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


def test_equal_best_counts_as_plateau_at_zero_threshold(tmp_path, monkeypatch):
    controller = _controller(tmp_path, monkeypatch, perf_improvement_threshold=0.0)
    _add_successes(controller, fitness=1.0)

    result = controller.check_convergence()

    assert result["action"] == "request_meta_guidance"
    assert result["search_state"] == "watching"


def test_zero_watch_delay_suppresses_first_layer_parameter_adjustment(tmp_path, monkeypatch):
    controller = _controller(tmp_path, monkeypatch, perf_improvement_threshold=0.0)
    _add_successes(controller, fitness=1.0)

    controller.check_convergence()
    signal = controller._selection_strategy_signal()

    assert signal.reason == "watching_request_meta_guidance"
    assert signal.tau_multiplier == 1.0
    assert signal.p_migrate_multiplier == 1.0
    assert signal.T_insp_multiplier == 1.0
    assert signal.p_exploit_override is None


def test_positive_watch_delay_keeps_first_layer_parameter_adjustment(tmp_path, monkeypatch):
    controller = _controller(
        tmp_path,
        monkeypatch,
        watch_tasks=3,
        perf_improvement_threshold=0.0,
        patience=4,
    )
    _add_successes(controller, fitness=1.0)

    result = controller.check_convergence()
    signal = controller._selection_strategy_signal()

    assert result["action"] == "continue"
    assert signal.reason == "watching_boost_exploration"
    assert signal.tau_multiplier == 1.5
    assert signal.p_migrate_multiplier == 2.0
    assert signal.T_insp_multiplier == 1.3


def test_late_watching_observation_uses_plateau_count_for_guidance_delay(tmp_path, monkeypatch):
    controller = _controller(
        tmp_path,
        monkeypatch,
        watch_tasks=3,
        perf_improvement_threshold=0.0,
        patience=1,
    )
    _add_successes(controller, fitness=1.0, count=5)

    result = controller.check_convergence()

    assert result["action"] == "request_meta_guidance"
    assert result["diagnostics"]["plateau_count"] == 4


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


def test_meta_guidance_request_includes_current_best_artifact_excerpt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    controller = _controller(tmp_path, monkeypatch)
    artifact = tmp_path / "candidate.py"
    artifact.write_text("def solve():\n    return 'current-best-route'\n", encoding="utf-8")
    controller.db.add(
        SuccessRecord(
            id="success_with_code",
            run_id=controller.run_id,
            fitness=1.0,
            artifact_ref=str(artifact),
            created_at="2026-01-01T00:00:00+00:00",
        )
    )
    for index in range(1, 5):
        controller.db.add(
            SuccessRecord(
                id=f"success_without_code_{index}",
                run_id=controller.run_id,
                fitness=1.0,
                artifact_ref=f"missing_{index}.py",
                created_at=f"2026-01-01T00:00:0{index}+00:00",
            )
        )
    controller._load_state()

    convergence = controller.check_convergence()
    request = controller.get_meta_guidance_request(convergence["request_id"])

    assert request["current_best"]["artifact_ref"] == str(artifact)
    assert "current-best-route" in request["current_best"]["artifact_excerpt"]
    assert request["current_best"]["artifact_excerpt_truncated"] is False
    assert any("critically" in item.lower() for item in request["generation_instructions"])
    assert any(
        "verifier/evaluator source" in item and "may inspect" in item
        for item in request["generation_instructions"]
    )
    assert any(
        "may read verifier/evaluator source" in item.lower()
        for item in request["constraints"]
    )


def test_commit_guidance_rejects_duplicate_approach_types(tmp_path, monkeypatch):
    controller = _controller(tmp_path, monkeypatch)
    _add_successes(controller, fitness=1.0)
    convergence = controller.check_convergence()
    request = controller.get_meta_guidance_request(convergence["request_id"])

    result = controller.commit_meta_guidance(
        request["request_id"],
        {
            "guidance": [
                {
                    "idea": "Try one search route",
                    "description": "Use one route.",
                    "novelty_claim": "Different from current lineages.",
                    "implementation_plan": ["Implement route"],
                    "approach_type": "global_search",
                    "max_uses": 1,
                },
                {
                    "idea": "Try a second search route",
                    "description": "Use another route in the same family.",
                    "novelty_claim": "Different parameters.",
                    "implementation_plan": ["Implement another route"],
                    "approach_type": "Global_Search",
                    "max_uses": 1,
                },
            ]
        },
    )

    assert result == {"committed": False, "reason": "duplicate_approach_type"}


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


def test_active_guidance_uses_latest_best_parent(tmp_path, monkeypatch):
    controller = _controller(tmp_path, monkeypatch)
    _add_successes(controller, fitness=1.0)
    convergence = controller.check_convergence()
    request = controller.get_meta_guidance_request(convergence["request_id"])
    controller.commit_meta_guidance(
        request["request_id"],
        {
            "guidance": [
                {
                    "idea": "Try a representation switch",
                    "description": "Apply the same high-level idea to the current best.",
                    "novelty_claim": "Recent attempts have not changed representation.",
                    "implementation_plan": ["Switch representation", "Preserve output contract"],
                    "approach_type": "representation",
                    "max_uses": 2,
                }
            ]
        },
    )

    first_parent = controller.select_parent("task_151")
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
    second_parent = controller.select_parent("task_152")
    controller.meta_guidance.load()
    assignments = controller.meta_guidance.state["task_assignments"]
    active = controller.meta_guidance.state["active_guidance"][0]

    assert first_parent["parent_id"] == "success_0000"
    assert second_parent["parent_id"] == "success_new_best"
    assert assignments["task_151"]["anchor_parent_id"] == "success_0000"
    assert assignments["task_152"]["anchor_parent_id"] == "success_new_best"
    assert assignments["task_151"]["anchor_fitness"] == 1.0
    assert assignments["task_152"]["anchor_fitness"] == 2.0
    assert active["creation_anchor_parent_id"] == "success_0000"
    assert active["uses"] == 2


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
    tried = controller.meta_guidance.state["tried_guidance"][0]
    assert tried["outcome"] == "new_best"
    assert tried["outcomes"][0]["anchor_fitness"] == 1.0
    assert tried["outcomes"][0]["delta_vs_anchor"] == 1.0

    report = controller.finalize_run()
    summary_path = tmp_path / report["summary_ref"]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert summary["meta_evolve"]["new_best_from_guidance"] == 1
    assert summary["attempts"][-1]["meta_guidance_id"] == "mg_0001"


def test_next_meta_request_includes_guidance_outcomes_and_notes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    controller = _controller(tmp_path, monkeypatch)
    _add_successes(controller, fitness=1.0)
    first = controller.check_convergence()
    request = controller.get_meta_guidance_request(first["request_id"])
    controller.commit_meta_guidance(
        request["request_id"],
        {
            "guidance": [
                {
                    "idea": "Try bounded structural variation",
                    "description": "Change structure without changing the output contract.",
                    "target_metric": "fitness",
                    "novelty_claim": "Current attempts use the same structure.",
                    "current_best_assessment": "The current best works but may trap the search in one representation.",
                    "relationship_to_current_best": "depart",
                    "implementation_plan": ["Change structure", "Preserve interface"],
                    "cautions": ["Avoid repeating the same representation"],
                    "approach_type": "structured_variation",
                    "max_uses": 1,
                }
            ]
        },
    )
    controller.select_parent("task_401")
    result_path = tmp_path / "same_score.json"
    result_path.write_text(
        json.dumps(
            {
                "status": "success",
                "artifact_ref": "runs/run_meta_test/tasks/task_401/final.py",
                "fitness": 1.0,
                "summary": "valid but no best improvement",
            }
        ),
        encoding="utf-8",
    )
    controller.record_attempt(
        task_id="task_401",
        verification_result_ref=str(result_path),
        attempt_id="attempt_001",
        max_attempts=3,
    )
    notes_file = notes_path(controller.run_id)
    notes_file.parent.mkdir(parents=True, exist_ok=True)
    notes_file.write_text(
        json.dumps(
            {
                "schema_version": "notes_run_local",
                "notes": [
                    {
                        "note_id": "note_positive_1",
                        "note_type": "positive",
                        "title": "Preserve useful vectorization",
                        "lesson": "Vectorized candidate generation helped earlier attempts.",
                        "actionable_guidance": "Keep vectorized scoring when changing the search family.",
                        "evidence_summary": "Observed in a previous successful task.",
                        "confidence": "medium",
                        "trigger": {
                            "kind": "material_improvement",
                            "relative_delta": 0.05,
                            "child_fitness": 1.0,
                            "parent_fitness": 0.95,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    second = controller.check_convergence()
    second_request = controller.get_meta_guidance_request(second["request_id"])

    assert second["action"] == "request_meta_guidance"
    history = second_request["guidance_history"]
    assert history[0]["idea"] == "Try bounded structural variation"
    assert history[0]["outcome"] == "regression"
    assert history[0]["outcome_summary"]["best_delta_vs_anchor"] == 0.0
    assert second_request["failed_guidance"][0]["outcome"] == "regression"
    assert second_request["notes_summary"]["positive"][0]["note_id"] == "note_positive_1"


def test_guidance_can_improve_its_anchor_without_creating_global_best(tmp_path, monkeypatch):
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
                    "idea": "Try an alternate numerical representation",
                    "description": "Change the representation while preserving the interface.",
                    "novelty_claim": "Current attempts share one representation.",
                    "implementation_plan": ["Change representation", "Preserve output contract"],
                    "approach_type": "representation",
                    "max_uses": 1,
                }
            ]
        },
    )
    controller.select_parent("task_501")
    controller.db.add(
        SuccessRecord(
            id="concurrent_best",
            run_id=controller.run_id,
            fitness=2.0,
            artifact_ref="concurrent_best.py",
            created_at="2026-01-01T00:01:00+00:00",
        )
    )
    controller._load_state()

    result_path = tmp_path / "improved_anchor.json"
    result_path.write_text(
        json.dumps(
            {
                "status": "success",
                "artifact_ref": "runs/run_meta_test/tasks/task_501/final.py",
                "fitness": 1.5,
            }
        ),
        encoding="utf-8",
    )
    controller.record_attempt(
        task_id="task_501",
        verification_result_ref=str(result_path),
        attempt_id="attempt_001",
        max_attempts=1,
    )
    controller.meta_guidance.load()

    tried = controller.meta_guidance.state["tried_guidance"][0]
    assert tried["outcome"] == "improved_anchor"
    assert tried["outcomes"][0]["anchor_fitness"] == 1.0
    assert tried["outcomes"][0]["best_fitness_before"] == 2.0
    assert tried["outcomes"][0]["delta_vs_anchor"] == 0.5
    assert tried["outcomes"][0]["created_new_best"] is False


def test_guidance_with_only_invalid_results_is_failed(tmp_path, monkeypatch):
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
                    "idea": "Try a different solver family",
                    "description": "Replace the current solver family.",
                    "novelty_claim": "The current family has plateaued.",
                    "implementation_plan": ["Replace solver", "Preserve output contract"],
                    "approach_type": "solver_family",
                    "max_uses": 1,
                }
            ]
        },
    )
    controller.select_parent("task_601")
    result_path = tmp_path / "invalid.json"
    result_path.write_text(
        json.dumps(
            {
                "status": "failure",
                "artifact_ref": "runs/run_meta_test/tasks/task_601/final.py",
                "misc": {"error": "invalid output"},
            }
        ),
        encoding="utf-8",
    )
    controller.record_attempt(
        task_id="task_601",
        verification_result_ref=str(result_path),
        attempt_id="attempt_001",
        max_attempts=1,
    )
    controller.meta_guidance.load()

    tried = controller.meta_guidance.state["tried_guidance"][0]
    assert tried["outcome"] == "failed"
    assert tried["outcome_summary"]["valid_uses"] == 0
    assert tried["outcome_summary"]["failed_uses"] == 1

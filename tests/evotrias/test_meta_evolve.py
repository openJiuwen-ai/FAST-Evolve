# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
from __future__ import annotations

import json

from evotrias.db.attempts import AttemptRecord, Attempts
from evotrias.db.config import Config
from evotrias.db.meta_guidance_events import MetaGuidanceEvents
from evotrias.db.meta_guidance_state import Guidance, MetaGuidanceState
from evotrias.db.note_requests import NoteRequest, NoteRequests
from evotrias.db.notes import Note, Notes, NoteTrigger
from evotrias.db.notes_events import NotesEvents
from evotrias.db.task_contexts import TaskContexts
from evotrias.evolve_ctrl import controller as ctrl
from evotrias.evolve_ctrl import meta_guidance as meta_guidance_module
from evotrias.evolve_ctrl.inspiration_selector import LineageBoltzmannInspirationSelector, _inspiration
from evotrias.evolve_ctrl.parent_selector import LineageAwareParentSelector
from evotrias.evolve_ctrl.success_pool import SuccessPool
from evotrias.utils.time import utc_now_iso


def _run(
    tmp_path,
    monkeypatch,
    *,
    max_total_tasks=100,
    perf_improvement_threshold=0.1,
    patience=1,
    meta_enabled=True,
) -> str:
    monkeypatch.chdir(tmp_path)
    run_id = "run_meta_test"
    ctrl.configure_run(
        run_id,
        task_info={"task_description": "optimize the test task"},
        configs={"max_total_tasks": max_total_tasks, "lineage_root_number": 1, "max_concurrent_tasks": 4},
    )
    with Config(run_id) as config:
        # These tests model an established run in its evolution phase; a zero
        # root number means the root phase is already over, so select_parent
        # is never root-gated. configure_run clamps it to >= 1, so set it here.
        config.scheduler.lineage_root_number = 0
        config.plateau.window = 1
        config.plateau.improvement_threshold = perf_improvement_threshold
        config.plateau.patience = patience
        config.meta_evolve.enabled = meta_enabled
        config.meta_evolve.guidance_num_to_generate = 3
        config.meta_evolve.guidance_max_uses = 2
    return run_id


def _add_success(
    run_id: str,
    task_id: str,
    *,
    fitness=1.0,
    parent_id=None,
    artifact_ref=None,
    metrics=None,
    created_at=None,
):
    """Record one attempt that entered the success pool; the pool is rebuilt from it."""
    Attempts(run_id).append(
        AttemptRecord(
            id="attempt_001",
            run_id=run_id,
            task_id=task_id,
            subagent_id="agent",
            artifact_ref=artifact_ref or f"{task_id}.py",
            parent_id=parent_id,
            inspiration_ids=[],
            generation=0,
            verification_result={"status": "success", "fitness": fitness, "metrics": metrics or {}},
            in_success_pool=True,
            created_at=created_at or utc_now_iso(),
        )
    )


def _add_successes(run_id: str, *, fitness=1.0, count=5):
    for index in range(count):
        _add_success(
            run_id,
            f"success_{index:04d}",
            fitness=fitness,
            artifact_ref=f"artifact_{index}.py",
            created_at=f"2026-01-01T00:00:0{index}+00:00",
        )


def test_exhausted_guidance_history_is_never_truncated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_id = "run_guidance_history"

    for index in range(1, 12):
        guidance_id = f"mg_{index:04d}"
        with MetaGuidanceState(run_id) as state:
            state.active_guidance = [
                Guidance(
                    idea=f"idea {index}",
                    description=f"description {index}",
                    novelty_claim="",
                    approach_type="",
                    implementation_plan=[f"step {index}"],
                    guidance_id=guidance_id,
                    anchor_parent_id="success_0000",
                    uses=1,
                    max_uses=1,
                )
            ]
            meta_guidance_module._archive_exhausted_if_ready(state, guidance_id)

    tried = MetaGuidanceState(run_id).tried_guidance
    assert len(tried) == 11
    assert [item.guidance_id for item in tried] == [f"mg_{index:04d}" for index in range(1, 12)]
    assert tried[0].idea == "idea 1"
    assert tried[0].description == "description 1"
    assert tried[0].implementation_plan == ["step 1"]


def test_plateau_requests_guidance_without_stop(tmp_path, monkeypatch):
    run_id = _run(tmp_path, monkeypatch)
    _add_successes(run_id)

    result = ctrl.check_convergence(run_id, concurrency=1)

    assert result["action"] == "request_meta_guidance"
    assert result["search_state"] == "watching"
    assert result["request_id"].startswith("mgr_")


def test_equal_best_counts_as_plateau_at_zero_threshold(tmp_path, monkeypatch):
    run_id = _run(tmp_path, monkeypatch, perf_improvement_threshold=0.0)
    _add_successes(run_id, fitness=1.0)

    result = ctrl.check_convergence(run_id, concurrency=1)

    assert result["action"] == "request_meta_guidance"
    assert result["search_state"] == "watching"


def test_meta_disabled_observes_plateau_without_requesting_guidance(tmp_path, monkeypatch):
    run_id = _run(
        tmp_path,
        monkeypatch,
        perf_improvement_threshold=0.0,
        patience=1,
        meta_enabled=False,
    )
    _add_successes(run_id, fitness=1.0, count=5)

    result = ctrl.check_convergence(run_id, concurrency=1)

    assert result["action"] == "continue"
    assert result["search_state"] == "watching"
    assert result["meta_guidance"]["enabled"] is False
    assert result["diagnostics"]["plateau_count"] == 4
    assert result["diagnostics"]["plateau_detected"] is True
    assert "s2_diversity_change" not in result["diagnostics"]
    assert "s3_activity" not in result["diagnostics"]


def test_hard_budget_still_stops(tmp_path, monkeypatch):
    run_id = _run(tmp_path, monkeypatch, max_total_tasks=1)
    with TaskContexts(run_id) as contexts:
        contexts.get("task_001", create=True)

    result = ctrl.check_convergence(run_id, concurrency=1)

    assert result["action"] == "budget_exhausted"
    assert result["search_state"] == "stopped"


def test_retries_do_not_consume_task_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("EVOTRIAS_NOTES_ENABLED", "0")
    run_id = _run(tmp_path, monkeypatch, max_total_tasks=3)

    for index in range(1, 4):
        result_path = tmp_path / f"attempt_{index:03d}.json"
        result_path.write_text(
            json.dumps(
                {
                    "status": "failure",
                    "artifact_ref": f"runs/run_meta_test/attempt_{index:03d}/candidate.py",
                    "fitness": None,
                }
            ),
            encoding="utf-8",
        )
        ctrl.record_attempt(
            run_id,
            task_id="task_001",
            attempt_id=f"attempt_{index:03d}",
            artifact_ref=None,
            verification_result_ref=str(result_path),
            max_attempts=3,
        )

    assert len(Attempts(run_id)) == 3
    assert ctrl._task_count(run_id) == 1
    assert ctrl._progress(run_id, Config(run_id)) == 1 / 3
    assert ctrl.check_convergence(run_id, concurrency=1)["action"] == "continue"


def test_second_success_for_same_task_does_not_reenter_pool(tmp_path, monkeypatch):
    monkeypatch.setenv("EVOTRIAS_NOTES_ENABLED", "0")
    run_id = _run(tmp_path, monkeypatch, max_total_tasks=5)

    def record(attempt_id: str, fitness: float):
        result_path = tmp_path / f"{attempt_id}.json"
        result_path.write_text(
            json.dumps(
                {
                    "status": "success",
                    "artifact_ref": f"runs/run_meta_test/{attempt_id}/candidate.py",
                    "fitness": fitness,
                }
            ),
            encoding="utf-8",
        )
        return ctrl.record_attempt(
            run_id,
            task_id="task_001",
            attempt_id=attempt_id,
            artifact_ref=None,
            verification_result_ref=str(result_path),
            max_attempts=3,
        )

    first = record("attempt_001", 1.0)
    second = record("attempt_002", 2.0)

    assert first["in_success_pool"] is True
    assert second["in_success_pool"] is False
    assert second["success_record_id"] is None
    flags = [a.in_success_pool for a in Attempts(run_id)]
    assert flags == [True, False]
    pool = SuccessPool(run_id)
    assert [r.id for r in pool.get_all()] == ["task_001"]
    assert pool.get("task_001").fitness == 1.0


def test_commit_generated_note_is_idempotent_per_request(tmp_path, monkeypatch):
    run_id = _run(tmp_path, monkeypatch)
    NoteRequests(run_id).append(
        NoteRequest(
            note_request_id="note_req_task_001_attempt_001_positive",
            note_id="note_task_001_attempt_001_positive",
            run_id=run_id,
            task_id="task_001",
            attempt_id="attempt_001",
            note_type="positive",
        )
    )
    generated = {
        "title": "Keep the stable update",
        "lesson": "It improved fitness.",
        "actionable_guidance": "Reuse it.",
        "evidence_summary": "Seen once.",
        "confidence": "medium",
    }
    first = ctrl.commit_generated_note(run_id, "note_req_task_001_attempt_001_positive", generated)
    second = ctrl.commit_generated_note(run_id, "note_req_task_001_attempt_001_positive", generated)

    assert first == {"committed": True, "note_id": "note_task_001_attempt_001_positive"}
    assert second["committed"] is True and second["already_committed"] is True
    assert [n.note_id for n in Notes(run_id)] == ["note_task_001_attempt_001_positive"]
    assert [e.event_type for e in NotesEvents(run_id)] == ["note_committed"]


def test_commit_guidance_uses_canonical_best_anchor(tmp_path, monkeypatch):
    run_id = _run(tmp_path, monkeypatch)
    _add_successes(run_id, fitness=1.0)

    convergence = ctrl.check_convergence(run_id, concurrency=1)
    request = ctrl.get_meta_guidance_request(run_id, convergence["request_id"])
    commit = ctrl.commit_meta_guidance(
        run_id,
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

    parent = ctrl.select_parent(run_id, "task_100")

    assert commit["committed"] is True
    assert commit["anchor_parent_id"] == "success_0000"
    assert parent["parent_id"] == "success_0000"
    assert parent["artifact_ref"] == "artifact_0.py"


def test_parent_and_inspirations_expose_persisted_metrics(tmp_path, monkeypatch):
    run_id = _run(tmp_path, monkeypatch)
    _add_success(
        run_id,
        "parent",
        fitness=2.0,
        artifact_ref="parent.py",
        metrics={"total_cost": 10.0, "scenario": {"a": 4.0}},
        created_at="2026-01-01T00:00:00+00:00",
    )
    _add_success(
        run_id,
        "inspiration",
        fitness=1.0,
        parent_id="parent",
        artifact_ref="inspiration.py",
        metrics={"total_cost": 12.0},
        created_at="2026-01-01T00:00:01+00:00",
    )

    monkeypatch.setattr(
        LineageAwareParentSelector,
        "select",
        lambda self, pool, progress: pool.get("parent"),
    )
    monkeypatch.setattr(
        LineageBoltzmannInspirationSelector,
        "select",
        lambda self, parent, pool, progress: [
            {**_inspiration(parent), "is_parent": True},
            {**_inspiration(pool.get("inspiration")), "is_parent": False},
        ],
    )

    parent = ctrl.select_parent(run_id, "task_metrics")
    inspirations = ctrl.select_inspirations(run_id, "task_metrics")

    assert parent["metrics"] == {"total_cost": 10.0, "scenario": {"a": 4.0}}
    assert inspirations["inspirations"][0]["metrics"] == {"total_cost": 12.0}


def test_commit_guidance_accepts_json_string_and_rejects_bad_max_uses(tmp_path, monkeypatch):
    run_id = _run(tmp_path, monkeypatch)
    _add_successes(run_id, fitness=1.0)
    convergence = ctrl.check_convergence(run_id, concurrency=1)
    request = ctrl.get_meta_guidance_request(run_id, convergence["request_id"])

    bad = ctrl.commit_meta_guidance(
        run_id,
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
    good = ctrl.commit_meta_guidance(
        run_id,
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
    run_id = _run(tmp_path, monkeypatch)
    artifact = tmp_path / "candidate.py"
    artifact.write_text("def solve():\n    return 'current-best-route'\n", encoding="utf-8")
    _add_success(
        run_id, "success_with_code", fitness=1.0, artifact_ref=str(artifact), created_at="2026-01-01T00:00:00+00:00"
    )
    for index in range(1, 5):
        _add_success(
            run_id,
            f"success_without_code_{index}",
            fitness=1.0,
            artifact_ref=f"missing_{index}.py",
            created_at=f"2026-01-01T00:00:0{index}+00:00",
        )

    convergence = ctrl.check_convergence(run_id, concurrency=1)
    request = ctrl.get_meta_guidance_request(run_id, convergence["request_id"])

    assert request["current_best"]["artifact_ref"] == str(artifact)
    assert "current-best-route" in request["current_best"]["artifact_excerpt"]
    assert request["current_best"]["artifact_excerpt_truncated"] is False
    assert any("critically" in item.lower() for item in request["generation_instructions"])
    assert any("current_best.metrics" in item for item in request["generation_instructions"])
    assert all("verifier/evaluator source" not in item.lower() for item in request["generation_instructions"])
    assert any("branch produced the evaluated result" in item for item in request["generation_instructions"])
    assert any(
        "no_effect" in item and "does not by itself prove" in item for item in request["generation_instructions"]
    )
    assert all("may read verifier/evaluator source" not in item.lower() for item in request["constraints"])


def test_pending_request_on_disk_has_no_artifact_excerpt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_id = _run(tmp_path, monkeypatch)
    artifact = tmp_path / "candidate.py"
    artifact.write_text("def solve():\n    return 'current-best-route'\n", encoding="utf-8")
    _add_success(
        run_id, "success_with_code", fitness=1.0, artifact_ref=str(artifact), created_at="2026-01-01T00:00:00+00:00"
    )
    for index in range(1, 5):
        _add_success(
            run_id,
            f"success_without_code_{index}",
            fitness=1.0,
            artifact_ref=f"missing_{index}.py",
            created_at=f"2026-01-01T00:00:0{index}+00:00",
        )

    convergence = ctrl.check_convergence(run_id, concurrency=1)

    on_disk = MetaGuidanceState(run_id).pending_request.current_best
    assert set(on_disk) == {"record_id", "fitness", "artifact_ref", "summary", "metrics", "profile", "created_at"}
    served = ctrl.get_meta_guidance_request(run_id, convergence["request_id"])["current_best"]
    assert "current-best-route" in served["artifact_excerpt"]
    assert served["artifact_excerpt_truncated"] is False


def test_commit_guidance_rejects_duplicate_approach_types(tmp_path, monkeypatch):
    run_id = _run(tmp_path, monkeypatch)
    _add_successes(run_id, fitness=1.0)
    convergence = ctrl.check_convergence(run_id, concurrency=1)
    request = ctrl.get_meta_guidance_request(run_id, convergence["request_id"])

    result = ctrl.commit_meta_guidance(
        run_id,
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
    run_id = _run(tmp_path, monkeypatch)
    _add_successes(run_id, fitness=1.0)
    convergence = ctrl.check_convergence(run_id, concurrency=1)
    request = ctrl.get_meta_guidance_request(run_id, convergence["request_id"])
    ctrl.commit_meta_guidance(
        run_id,
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

    first = ctrl.select_parent(run_id, "task_101")
    second = ctrl.select_parent(run_id, "task_101")
    active = MetaGuidanceState(run_id).active_guidance[0]

    assert first == second
    assert active.uses == 1


def test_active_guidance_uses_latest_best_parent(tmp_path, monkeypatch):
    run_id = _run(tmp_path, monkeypatch)
    _add_successes(run_id, fitness=1.0)
    convergence = ctrl.check_convergence(run_id, concurrency=1)
    request = ctrl.get_meta_guidance_request(run_id, convergence["request_id"])
    ctrl.commit_meta_guidance(
        run_id,
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

    first_parent = ctrl.select_parent(run_id, "task_151")
    _add_success(
        run_id, "success_new_best", fitness=2.0, artifact_ref="new_best.py", created_at="2026-01-01T00:01:00+00:00"
    )
    second_parent = ctrl.select_parent(run_id, "task_152")
    meta_state = MetaGuidanceState(run_id)
    assignments = meta_state.task_assignments
    active = meta_state.active_guidance[0]

    assert first_parent["parent_id"] == "success_0000"
    assert second_parent["parent_id"] == "success_new_best"
    assert assignments["task_151"].anchor_parent_id == "success_0000"
    assert assignments["task_152"].anchor_parent_id == "success_new_best"
    assert assignments["task_151"].anchor_fitness == 1.0
    assert assignments["task_152"].anchor_fitness == 2.0
    assert active.creation_anchor_parent_id == "success_0000"
    assert active.uses == 2


def test_no_new_request_while_assigned_guidance_is_unresolved(tmp_path, monkeypatch):
    run_id = _run(tmp_path, monkeypatch)
    _add_successes(run_id, fitness=1.0)
    convergence = ctrl.check_convergence(run_id, concurrency=1)
    request = ctrl.get_meta_guidance_request(run_id, convergence["request_id"])
    ctrl.commit_meta_guidance(
        run_id,
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

    ctrl.select_parent(run_id, "task_201")
    ctrl.select_parent(run_id, "task_202")
    assert MetaGuidanceState(run_id).active_guidance[0].uses == 2

    followup = ctrl.check_convergence(run_id, concurrency=1)

    assert followup["action"] == "continue"
    assert followup["meta_guidance"]["active_count"] == 1


def test_pending_request_is_re_emitted_until_committed(tmp_path, monkeypatch):
    run_id = _run(tmp_path, monkeypatch)
    _add_successes(run_id, fitness=1.0)

    first = ctrl.check_convergence(run_id, concurrency=1)
    second = ctrl.check_convergence(run_id, concurrency=1)

    assert first["action"] == "request_meta_guidance"
    assert second["action"] == "request_meta_guidance"
    assert second["request_id"] == first["request_id"]


def test_convergence_returns_compact_meta_worker_dispatch_envelope(tmp_path, monkeypatch):
    run_id = _run(tmp_path, monkeypatch)
    _add_successes(run_id, fitness=1.0)

    result = ctrl.check_convergence(run_id, concurrency=1)

    assert result["action"] == "request_meta_guidance"
    assert result["request_id"].startswith("mgr_")
    assert result["meta_guidance"]["has_pending_request"] is True
    assert "task_description" not in result
    assert "current_best" not in result
    assert "artifact_excerpt" not in json.dumps(result)


def test_meta_commit_has_compact_postcondition_before_replenishment(tmp_path, monkeypatch):
    run_id = _run(tmp_path, monkeypatch)
    _add_successes(run_id, fitness=1.0)
    convergence = ctrl.check_convergence(run_id, concurrency=1)

    commit = ctrl.commit_meta_guidance(
        run_id,
        convergence["request_id"],
        {
            "guidance": [
                {
                    "idea": "Change the representation",
                    "description": "Try a structurally different representation.",
                    "novelty_claim": "Existing lineages share one representation.",
                    "implementation_plan": ["Build the representation", "Preserve output"],
                    "approach_type": "representation",
                    "max_uses": 2,
                }
            ]
        },
    )
    post_meta = ctrl.check_convergence(run_id, concurrency=1)

    assert commit["committed"] is True
    assert post_meta["action"] == "continue"
    assert post_meta["meta_guidance"]["has_pending_request"] is False
    assert post_meta["meta_guidance"]["active_count"] == 1
    assert "request_id" not in post_meta


def test_rejected_meta_commit_remains_pending_for_later_event(tmp_path, monkeypatch):
    run_id = _run(tmp_path, monkeypatch)
    _add_successes(run_id, fitness=1.0)
    convergence = ctrl.check_convergence(run_id, concurrency=1)

    rejected = ctrl.commit_meta_guidance(
        run_id,
        convergence["request_id"],
        {"guidance": []},
    )
    post_meta = ctrl.check_convergence(run_id, concurrency=1)

    assert rejected == {"committed": False, "reason": "empty_guidance"}
    assert post_meta["action"] == "request_meta_guidance"
    assert post_meta["request_id"] == convergence["request_id"]
    assert post_meta["meta_guidance"]["has_pending_request"] is True
    assert post_meta["meta_guidance"]["active_count"] == 0


def test_pending_request_is_cleared_after_new_best(tmp_path, monkeypatch):
    run_id = _run(tmp_path, monkeypatch)
    _add_successes(run_id, fitness=1.0)
    first = ctrl.check_convergence(run_id, concurrency=1)
    assert first["action"] == "request_meta_guidance"

    _add_success(
        run_id, "success_new_best", fitness=2.0, artifact_ref="new_best.py", created_at="2026-01-01T00:01:00+00:00"
    )
    after_improvement = ctrl.check_convergence(run_id, concurrency=1)
    request = ctrl.get_meta_guidance_request(run_id, first["request_id"])

    assert after_improvement["action"] == "continue"
    assert after_improvement["search_state"] == "exploring"
    assert request["available"] is False


def test_meta_guidance_events_cover_the_whole_lifecycle(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_id = _run(tmp_path, monkeypatch)
    _add_successes(run_id, fitness=1.0)
    convergence = ctrl.check_convergence(run_id, concurrency=1)
    request = ctrl.get_meta_guidance_request(run_id, convergence["request_id"])
    ctrl.commit_meta_guidance(
        run_id,
        request["request_id"],
        {
            "guidance": [
                {
                    "idea": "Switch representation",
                    "description": "Change representation first.",
                    "novelty_claim": "Nobody tried it.",
                    "implementation_plan": ["Build alternate representation"],
                    "approach_type": "representation",
                    "max_uses": 1,
                }
            ]
        },
    )
    ctrl.select_parent(run_id, "task_301")
    result_path = tmp_path / "eval_result.json"
    result_path.write_text(
        json.dumps({"status": "success", "artifact_ref": "runs/run_meta_test/tasks/task_301/final.py", "fitness": 2.0}),
        encoding="utf-8",
    )
    ctrl.record_attempt(
        run_id,
        task_id="task_301",
        verification_result_ref=str(result_path),
        attempt_id="attempt_001",
        artifact_ref=None,
        max_attempts=3,
    )
    ctrl.check_convergence(run_id, concurrency=1)  # new best ends the plateau -> watching_reset

    events = list(MetaGuidanceEvents(run_id))
    assert [e.event_type for e in events] == [
        "watching_started",
        "request_created",
        "guidance_committed",
        "assigned",
        "recorded",
        "guidance_archived",
        "watching_reset",
    ]
    by_type = {e.event_type: e.payload for e in events}
    assert by_type["request_created"]["request_id"] == request["request_id"]
    assert by_type["guidance_committed"]["approach_type"] == "representation"
    assert by_type["assigned"]["task_id"] == "task_301"
    assert by_type["recorded"]["created_new_best"] is True
    assert by_type["guidance_archived"]["outcome"] == "new_best"
    assert by_type["watching_reset"]["had_pending_request"] is False


def test_attempt_records_meta_guidance_and_report_summary(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_id = _run(tmp_path, monkeypatch)
    _add_successes(run_id, fitness=1.0)
    convergence = ctrl.check_convergence(run_id, concurrency=1)
    request = ctrl.get_meta_guidance_request(run_id, convergence["request_id"])
    ctrl.commit_meta_guidance(
        run_id,
        request["request_id"],
        {
            "guidance": [
                {
                    "idea": "Switch representation",
                    "description": "Change representation before applying the existing objective.",
                    "target_metric": "fitness",
                    "novelty_claim": "The current bests use the same representation.",
                    "implementation_plan": [
                        "Build alternate representation",
                        "Convert output back",
                    ],
                    "cautions": ["Keep output contract unchanged"],
                    "approach_type": "representation",
                    "max_uses": 1,
                }
            ]
        },
    )
    parent = ctrl.select_parent(run_id, "task_301")
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
    record = ctrl.record_attempt(
        run_id,
        task_id="task_301",
        verification_result_ref=str(result_path),
        attempt_id="attempt_001",
        artifact_ref=None,
        max_attempts=3,
    )
    attempts = list(Attempts(run_id))

    assert record["in_success_pool"] is True
    assert attempts[-1].meta_guidance_id == "mg_0001"
    assert attempts[-1].anchor_parent_id == "success_0000"
    tried = MetaGuidanceState(run_id).tried_guidance[0]
    assert tried.outcome == "new_best"
    assert tried.outcomes[0]["anchor_fitness"] == 1.0
    assert tried.outcomes[0]["delta_vs_anchor"] == 1.0

    report = ctrl.finalize_run(run_id, None)
    summary_path = tmp_path / report["summary_ref"]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert summary["meta_evolve"]["new_best_from_guidance"] == 1
    assert summary["attempts"][-1]["meta_guidance_id"] == "mg_0001"


def test_next_meta_request_includes_guidance_outcomes_and_notes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_id = _run(tmp_path, monkeypatch)
    _add_successes(run_id, fitness=1.0)
    first = ctrl.check_convergence(run_id, concurrency=1)
    request = ctrl.get_meta_guidance_request(run_id, first["request_id"])
    ctrl.commit_meta_guidance(
        run_id,
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
    ctrl.select_parent(run_id, "task_401")
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
    ctrl.record_attempt(
        run_id,
        task_id="task_401",
        verification_result_ref=str(result_path),
        attempt_id="attempt_001",
        artifact_ref=None,
        max_attempts=3,
    )
    Notes(run_id).append(
        Note(
            note_id="note_positive_1",
            note_type="positive",
            title="Preserve useful vectorization",
            lesson="Vectorized candidate generation helped earlier attempts.",
            actionable_guidance="Keep vectorized scoring when changing the search family.",
            evidence_summary="Observed in a previous successful task.",
            confidence="medium",
            trigger=NoteTrigger(
                kind="material_improvement",
                relative_delta=0.05,
                child_fitness=1.0,
                parent_fitness=0.95,
            ),
        )
    )

    second = ctrl.check_convergence(run_id, concurrency=1)
    second_request = ctrl.get_meta_guidance_request(run_id, second["request_id"])

    assert second["action"] == "request_meta_guidance"
    history = second_request["guidance_history"]
    assert history[0]["idea"] == "Try bounded structural variation"
    assert history[0]["outcome"] == "no_effect"
    assert history[0]["outcome_summary"]["best_delta_vs_anchor"] == 0.0
    assert history[0]["outcome_summary"]["no_effect_uses"] == 1
    assert second_request["failed_guidance"][0]["outcome"] == "no_effect"
    assert second_request["notes_summary"]["positive"][0]["note_id"] == "note_positive_1"


def test_equal_fitness_with_changed_metrics_is_regression(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_id = _run(tmp_path, monkeypatch)
    _add_successes(run_id, fitness=1.0)
    convergence = ctrl.check_convergence(run_id, concurrency=1)
    request = ctrl.get_meta_guidance_request(run_id, convergence["request_id"])
    ctrl.commit_meta_guidance(
        run_id,
        request["request_id"],
        {
            "guidance": [
                {
                    "idea": "Change the cost composition",
                    "description": "Preserve fitness while changing component behavior.",
                    "novelty_claim": "The component profile has not been explored.",
                    "implementation_plan": ["Change one component"],
                    "approach_type": "metric_profile",
                    "max_uses": 1,
                }
            ]
        },
    )
    ctrl.select_parent(run_id, "task_402")
    result_path = tmp_path / "same_fitness_changed_metrics.json"
    result_path.write_text(
        json.dumps(
            {
                "status": "success",
                "artifact_ref": "runs/run_meta_test/tasks/task_402/final.py",
                "fitness": 1.0,
                "metrics": {"component_cost": 2.0},
            }
        ),
        encoding="utf-8",
    )

    ctrl.record_attempt(
        run_id,
        task_id="task_402",
        verification_result_ref=str(result_path),
        attempt_id="attempt_001",
        artifact_ref=None,
        max_attempts=1,
    )

    tried = MetaGuidanceState(run_id).tried_guidance[0]
    assert tried.outcome == "regression"
    assert tried.outcomes[0]["metrics_changed"] is True
    assert tried.outcomes[0]["no_effect"] is False
    assert tried.outcome_summary["no_effect_uses"] == 0


def test_guidance_can_improve_its_anchor_without_creating_global_best(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_id = _run(tmp_path, monkeypatch)
    _add_successes(run_id, fitness=1.0)
    convergence = ctrl.check_convergence(run_id, concurrency=1)
    request = ctrl.get_meta_guidance_request(run_id, convergence["request_id"])
    ctrl.commit_meta_guidance(
        run_id,
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
    ctrl.select_parent(run_id, "task_501")
    _add_success(
        run_id,
        "concurrent_best",
        fitness=2.0,
        artifact_ref="concurrent_best.py",
        created_at="2026-01-01T00:01:00+00:00",
    )

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
    ctrl.record_attempt(
        run_id,
        task_id="task_501",
        verification_result_ref=str(result_path),
        attempt_id="attempt_001",
        artifact_ref=None,
        max_attempts=1,
    )

    tried = MetaGuidanceState(run_id).tried_guidance[0]
    assert tried.outcome == "improved_anchor"
    assert tried.outcomes[0]["anchor_fitness"] == 1.0
    assert tried.outcomes[0]["best_fitness_before"] == 2.0
    assert tried.outcomes[0]["delta_vs_anchor"] == 0.5
    assert tried.outcomes[0]["created_new_best"] is False


def test_guidance_with_only_invalid_results_is_failed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_id = _run(tmp_path, monkeypatch)
    _add_successes(run_id, fitness=1.0)
    convergence = ctrl.check_convergence(run_id, concurrency=1)
    request = ctrl.get_meta_guidance_request(run_id, convergence["request_id"])
    ctrl.commit_meta_guidance(
        run_id,
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
    ctrl.select_parent(run_id, "task_601")
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
    ctrl.record_attempt(
        run_id,
        task_id="task_601",
        verification_result_ref=str(result_path),
        attempt_id="attempt_001",
        artifact_ref=None,
        max_attempts=1,
    )

    tried = MetaGuidanceState(run_id).tried_guidance[0]
    assert tried.outcome == "failed"
    assert tried.outcome_summary["valid_uses"] == 0
    assert tried.outcome_summary["failed_uses"] == 1

"""Tests for evotrias.evolve_ctrl.inspiration_selector."""

from __future__ import annotations

import random

import pytest

from evotrias.db.attempts import AttemptRecord, Attempts
from evotrias.db.config import InspirationSelectionConfig
from evotrias.db.task_contexts import TaskContexts
from evotrias.evolve_ctrl.inspiration_selector import LineageBoltzmannInspirationSelector
from evotrias.evolve_ctrl.lineage_tree import LineageTree
from evotrias.evolve_ctrl.success_pool import SuccessPool, SuccessRecord

RUN_ID = "insp_sel"


@pytest.fixture(autouse=True)
def cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _seed(records):
    """Seed successes as (task_id, fitness, parent_id[, selection_count]); return (pool, tree).

    ``selection_count`` is derived by SuccessPool from task contexts whose
    ``parent_id`` points at the record, so it is seeded as that many contexts.
    """
    log = Attempts(RUN_ID)
    with TaskContexts(RUN_ID) as contexts:
        for index, spec in enumerate(records, start=1):
            task_id, fitness, parent_id, *rest = spec
            log.append(
                AttemptRecord(
                    id=f"attempt_{index:02d}",
                    run_id=RUN_ID,
                    task_id=task_id,
                    subagent_id="agent-1",
                    artifact_ref=f"runs/{RUN_ID}/tasks/{task_id}/attempts/attempt_01/solution.py",
                    parent_id=parent_id,
                    inspiration_ids=[],
                    generation=0,
                    verification_result={"status": "success", "fitness": fitness},
                    in_success_pool=True,
                    created_at=f"2026-01-01T00:00:{index:02d}+00:00",
                )
            )
            for n in range(rest[0] if rest else 0):
                child = contexts.get(f"{task_id}_sel{n}", create=True)
                child.parent_id = task_id
                child.parent_selected = True
    pool = SuccessPool(RUN_ID)
    tree = LineageTree()
    tree.rebuild(pool)
    return pool, tree


def _selector(tree, **overrides):
    return LineageBoltzmannInspirationSelector(InspirationSelectionConfig(**overrides), tree)


def test_only_parent_when_pool_has_one_record():
    pool, tree = _seed([("task_001", 0.5, None)])
    [only] = _selector(tree).select(pool.get("task_001"), pool, 0.0)
    assert only["id"] == "task_001"
    assert only["is_parent"] is True
    assert only["is_migration"] is False


def test_parent_never_repeated_and_no_duplicates():
    pool, tree = _seed(
        [
            ("task_001", 0.5, None),
            ("task_002", 0.6, "task_001"),
            ("task_003", 0.7, "task_001"),
            ("task_004", 0.8, "task_001"),
            ("task_005", 0.9, "task_001"),
        ]
    )
    result = _selector(tree, inspiration_sample_num=3, p_migrate=0.0).select(pool.get("task_001"), pool, 0.0)
    assert len(result) == 4
    assert len({item["id"] for item in result}) == 4
    assert result[0]["id"] == "task_001" and result[0]["is_parent"] is True
    assert all(item["is_parent"] is False and item["id"] != "task_001" for item in result[1:])


def test_sample_num_argument_overrides_config():
    pool, tree = _seed(
        [
            ("task_001", 0.5, None),
            ("task_002", 0.6, "task_001"),
            ("task_003", 0.7, "task_001"),
        ]
    )
    result = _selector(tree, inspiration_sample_num=3).select(pool.get("task_001"), pool, 0.0, sample_num=1)
    assert len(result) == 2


def test_migration_marks_foreign_and_orders_local_first(monkeypatch):
    pool, tree = _seed(
        [
            ("task_001", 0.5, None),
            ("task_002", 0.6, "task_001"),
            ("task_003", 0.4, "task_001"),
            ("task_004", 0.9, None),
            ("task_005", 0.8, None),
        ]
    )
    # p_migrate=1.0: slots migrate while a foreign elite is still unselected
    # (two foreign lineages, one elite each), then fall back to the local pool.
    monkeypatch.setattr(random, "choice", lambda seq: seq[0])
    monkeypatch.setattr(random, "random", lambda: 0.5)
    selector = _selector(tree, inspiration_sample_num=3, p_migrate=1.0, foreign_elite_num=1)
    result = selector.select(pool.get("task_001"), pool, 0.0)
    assert [item["id"] for item in result] == ["task_001", "task_002", "task_004", "task_005"]
    assert [item["is_migration"] for item in result] == [False, False, True, True]


def test_falls_back_to_foreign_when_lineage_is_alone():
    pool, tree = _seed(
        [
            ("task_001", 0.5, None),
            ("task_002", 0.9, None),
            ("task_003", 0.8, None),
        ]
    )
    # p_migrate=0.0 never migrates on purpose, but an empty local pool falls
    # back to any other record, which is then flagged as a migration.
    result = _selector(tree, inspiration_sample_num=2, p_migrate=0.0).select(pool.get("task_001"), pool, 0.0)
    assert len(result) == 3
    assert {item["id"] for item in result[1:]} == {"task_002", "task_003"}
    assert all(item["is_migration"] is True for item in result[1:])


def test_foreign_elites_take_top_k_per_lineage():
    pool, tree = _seed(
        [
            ("task_001", 0.5, None),
            ("task_002", 0.3, None),
            ("task_003", 0.9, "task_002"),
            ("task_004", 0.6, "task_002"),
            ("task_005", 0.8, None),
            ("task_006", 0.2, "task_005"),
            ("task_007", 0.7, "task_005"),
        ]
    )
    selector = _selector(tree, foreign_elite_num=2)
    elites = selector._build_foreign_elites(pool.get_all(), "task_001", "task_001")
    assert {record.id for record in elites} == {"task_003", "task_004", "task_005", "task_007"}


def test_boltzmann_low_temperature_picks_fittest(monkeypatch):
    sample = LineageBoltzmannInspirationSelector._boltzmann_sample
    candidates = [
        SuccessRecord(id="low", run_id=RUN_ID, fitness=0.2),
        SuccessRecord(id="high", run_id=RUN_ID, fitness=0.9),
        SuccessRecord(id="mid", run_id=RUN_ID, fitness=0.5),
    ]
    monkeypatch.setattr(random, "random", lambda: 0.5)
    assert sample(candidates, 1e-6).id == "high"
    assert sample(candidates[:1], 1e-6).id == "low"

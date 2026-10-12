"""Tests for evotrias.evolve_ctrl.parent_selector."""

from __future__ import annotations

import math
import random

import pytest

from evotrias.db.attempts import AttemptRecord, Attempts
from evotrias.db.config import ParentSelectionConfig
from evotrias.db.task_contexts import TaskContexts
from evotrias.evolve_ctrl.lineage_tree import LineageTree
from evotrias.evolve_ctrl.parent_selector import LineageAwareParentSelector
from evotrias.evolve_ctrl.success_pool import SuccessPool

RUN_ID = "parent_sel"


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
    return LineageAwareParentSelector(ParentSelectionConfig(**overrides), tree)


def test_empty_pool_returns_none():
    pool = SuccessPool(RUN_ID)
    assert _selector(LineageTree()).select(pool, 0.0) is None


def test_single_record_returned_without_sampling(monkeypatch):
    pool, tree = _seed([("task_001", 0.5, None)])
    monkeypatch.setattr(random, "random", lambda: pytest.fail("random must not be consulted"))
    assert _selector(tree).select(pool, 0.0).id == "task_001"


def test_single_lineage_picks_best_when_nothing_selected():
    pool, tree = _seed(
        [
            ("task_001", 0.5, None),
            ("task_002", 0.9, "task_001"),
            ("task_003", 0.7, "task_001"),
        ]
    )
    # One lineage: stage 1 short-circuits, stage 2 is pure UCB with zero
    # exploration terms, so rank quality alone decides.
    assert _selector(tree).select(pool, 0.0).id == "task_002"


def test_single_lineage_heavily_selected_elite_yields_to_sibling():
    pool, tree = _seed(
        [
            ("task_001", 0.5, None),
            ("task_002", 0.9, "task_001", 10),
            ("task_003", 0.7, "task_001"),
        ]
    )
    # quality: task_002 1.0, task_003 0.5; exploration with c_local=2 and
    # lineage count 10: task_002 ~0.9-1.4 (shielded), task_003 ~3.1.
    selector = _selector(tree, c_local_start=2.0, c_local_end=2.0)
    assert selector.select(pool, 0.0).id == "task_003"


def test_two_lineages_exploit_prefers_small_strong_lineage():
    pool, tree = _seed(
        [
            ("task_001", 1.0, None),
            ("task_002", 0.5, None),
            ("task_003", 0.5, "task_002"),
            ("task_004", 0.5, "task_002"),
        ]
    )
    # p_exploit=1.0 forces the deterministic branch: lineage task_001 has the
    # higher deserved share (best fitness) and the smaller actual share, so its
    # deficit * quality bonus wins over the larger, weaker lineage.
    selector = _selector(tree, p_exploit=1.0)
    assert selector.select(pool, 0.0).id == "task_001"


def test_anneal_endpoints_and_midpoint():
    anneal = LineageAwareParentSelector._anneal
    assert anneal(1.5, 0.5, 0.0) == 1.5
    assert anneal(1.5, 0.5, 1.0) == 0.5
    assert anneal(1.5, 0.5, 0.5) == pytest.approx(math.sqrt(0.75))
    assert anneal(1.5, 0.5, 2.0) == 0.5
    assert anneal(0.0, 0.5, 0.3) == 0.5


def test_softmax_sums_to_one_and_low_temperature_concentrates():
    softmax = LineageAwareParentSelector._softmax
    warm = softmax({"a": 1.0, "b": 0.0, "c": 0.5}, 1.0)
    assert sum(warm.values()) == pytest.approx(1.0)
    assert warm["a"] > warm["c"] > warm["b"]
    cold = softmax({"a": 1.0, "b": 0.0, "c": 0.5}, 1e-6)
    assert cold["a"] == pytest.approx(1.0)
    assert cold["b"] == pytest.approx(0.0)


def test_roulette_lands_by_cumulative_weight(monkeypatch):
    roulette = LineageAwareParentSelector._roulette
    weights = {"a": 1.0, "b": 3.0}
    monkeypatch.setattr(random, "random", lambda: 0.1)
    assert roulette(weights) == "a"
    monkeypatch.setattr(random, "random", lambda: 0.5)
    assert roulette(weights) == "b"

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).parents[1]

CASES = [
    ("circle_packing", {"sum_radii": 2.5, "target_ratio": 0.9}),
    ("circle_packing_rect", {"radii_sum": 2.3, "target_ratio": 0.8}),
    ("heilbronn_triangle", {"min_area_normalized": 0.03, "target_ratio": 0.85}),
    ("first_autocorr_ineq", {"c1": 0.25, "loss": 0.01, "n_points": 128, "target_ratio": 0.9}),
    ("second_autocorr_ineq", {"c2": 0.15, "loss": 0.02, "n_points": 128, "target_ratio": 0.8}),
    (
        "third_autocorr_ineq",
        {"c3": 0.1, "computed_c3": 0.11, "loss": 0.01, "n_points": 128, "target_ratio": 0.9},
    ),
    ("erdos_min_overlap", {"c5_bound": 0.2, "n_points": 64, "target_ratio": 0.75}),
    (
        "sums_diffs_finite_sets",
        {"c6_bound": 0.3, "set_size": 32, "max_val": 100, "target_ratio": 0.7},
    ),
    ("uncertainty_ineq", {"c4_bound": 0.4, "target_ratio": 0.8, "r_max": 1.2}),
    ("matmul", {"inverse_rank": 0.5, "target_ratio": 0.7, "loss": 0.03, "rank": 2}),
]


def _load_evaluator(skill_name: str):
    path = REPO_ROOT / "benchmarks" / "skills" / skill_name / "scripts" / "evaluator.py"
    spec = spec_from_file_location(f"{skill_name}_evaluator", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(("skill_name", "domain_metrics"), CASES)
def test_format_result_preserves_domain_metrics_and_lightweight_profile(skill_name, domain_metrics):
    evaluator = _load_evaluator(skill_name)
    raw = {
        "combined_score": 0.5,
        "validity": 1.0,
        "eval_time": 0.25,
        **domain_metrics,
        "run_id": "run_test",
    }

    formatted = evaluator.format_result(raw, "solution.py")

    assert formatted["fitness"] == 0.5
    assert formatted["metrics"] == {
        "validity": 1.0,
        "eval_time": 0.25,
        **domain_metrics,
    }
    assert formatted["profile"] == {"validity": 1.0, "eval_time": 0.25}
    assert formatted["misc"] == {"run_id": "run_test"}


def test_cloudcast_format_result_preserves_diagnostic_metrics():
    evaluator = _load_evaluator("cloudcast")
    domain_metrics = {
        "total_cost": 10.5,
        "egress_cost": 10.0,
        "instance_cost": 0.5,
        "per_config": {"example": {"total_cost": 10.5}},
        "topology_hash": "abc123",
    }

    formatted = evaluator.format_result(
        {
            "combined_score": 0.5,
            "validity": 1.0,
            "eval_time": 0.25,
            **domain_metrics,
        },
        "solution.py",
    )

    assert formatted["fitness"] == 0.5
    assert formatted["metrics"] == {
        "validity": 1.0,
        "eval_time": 0.25,
        **domain_metrics,
    }
    assert formatted["profile"] == {"validity": 1.0, "eval_time": 0.25}


def test_cloudcast_simulation_metrics_are_deterministic_and_decomposed():
    evaluator = _load_evaluator("cloudcast")
    graph = evaluator.nx.DiGraph()
    graph.add_edge("b", "c", cost=0.2, partitions={"1", "0"})
    graph.add_edge("a", "b", cost=0.1, partitions={"0", "1", "2"})
    simulator = SimpleNamespace(g=graph, partition_data_vol=10.0)

    metrics = evaluator._simulation_metrics(
        simulator,
        total_cost=7.5,
        max_transfer_time=3.25,
    )

    assert metrics["egress_cost"] == pytest.approx(7.0)
    assert metrics["instance_cost"] == pytest.approx(0.5)
    assert metrics["unique_edge_cost"] == pytest.approx(0.3)
    assert metrics["node_count"] == 3
    assert metrics["edge_count"] == 2
    assert metrics["partition_edge_count"] == 5
    assert metrics["max_transfer_time"] == pytest.approx(3.25)
    assert len(metrics["topology_hash"]) == 64

    reversed_graph = evaluator.nx.DiGraph()
    reversed_graph.add_edge("a", "b", cost=0.1, partitions={"2", "1", "0"})
    reversed_graph.add_edge("b", "c", cost=0.2, partitions={"0", "1"})
    reversed_simulator = SimpleNamespace(g=reversed_graph, partition_data_vol=10.0)
    reversed_metrics = evaluator._simulation_metrics(
        reversed_simulator,
        total_cost=7.5,
        max_transfer_time=3.25,
    )

    assert reversed_metrics["topology_hash"] == metrics["topology_hash"]

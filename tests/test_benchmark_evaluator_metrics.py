from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

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

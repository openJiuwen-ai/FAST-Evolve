from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

EVALUATOR_PATH = (
    Path(__file__).parents[1]
    / "benchmarks"
    / "skills"
    / "signal_processing"
    / "scripts"
    / "evaluator.py"
)
SPEC = spec_from_file_location("signal_processing_evaluator", EVALUATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
EVALUATOR = module_from_spec(SPEC)
SPEC.loader.exec_module(EVALUATOR)


def test_format_result_preserves_detailed_metrics_without_expanding_profile():
    raw = {
        "combined_score": 0.75,
        "composite_score": 0.8,
        "overall_score": 0.75,
        "target_ratio": 0.75,
        "slope_changes": 4.0,
        "lag_error": 0.1,
        "avg_error": 0.2,
        "false_reversals": 1.0,
        "correlation": 0.9,
        "noise_reduction": 0.6,
        "smoothness_score": 0.85,
        "accuracy_score": 0.9,
        "success_rate": 1.0,
        "eval_time": 0.25,
        "validity": 1.0,
        "run_id": "run_test",
    }

    formatted = EVALUATOR.format_result(raw, "solution.py")

    assert formatted["fitness"] == 0.75
    assert formatted["metrics"] == {
        key: raw[key]
        for key in [
            "validity",
            "eval_time",
            "target_ratio",
            "composite_score",
            "overall_score",
            "slope_changes",
            "lag_error",
            "avg_error",
            "false_reversals",
            "correlation",
            "noise_reduction",
            "smoothness_score",
            "accuracy_score",
            "success_rate",
        ]
    }
    assert formatted["profile"] == {"validity": 1.0, "eval_time": 0.25}
    assert formatted["misc"] == {"run_id": "run_test"}

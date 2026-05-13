#!/usr/bin/env python
"""
Sums and Differences of Finite Sets Evaluator

Usage:
    python evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref>

Output:
    - Success: prints result file path, exit code 0
    - Failure: prints error message, exit code 1
"""

import argparse
import json
import os
import sys
import time
import traceback
from typing import Any, Dict

import numpy as np

BENCHMARK = 1.158417281556896
TIMEOUT_SECONDS = 600


def validate_sums_diffs(u_set, c6_bound):
    """Validate the C6 solution. Returns True if valid, False otherwise."""
    if np.isnan(u_set).any() or np.isnan(c6_bound):
        return False
    if not isinstance(u_set, np.ndarray) or u_set.ndim != 1:
        return False
    if 0 not in u_set:
        return False
    if np.any(u_set < 0):
        return False
    u_plus_u = np.unique(u_set[:, None] + u_set[None, :])
    u_minus_u = np.unique(u_set[:, None] - u_set[None, :])
    size_U_plus_U = len(u_plus_u)
    size_U_minus_U = len(u_minus_u)
    max_U = np.max(u_set)
    ratio = size_U_minus_U / size_U_plus_U
    log_ratio = np.log(ratio)
    log_denom = np.log(2 * max_U + 1)
    computed_c6 = 1 + log_ratio / log_denom
    if not np.isclose(computed_c6, c6_bound):
        return False
    return True


def run_program(program_path: str):
    """Run the program and return results."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("program", program_path)
    program = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(program)

    u_set, c6_bound = program.run()
    return u_set, c6_bound


def evaluate(program_path: str, timeout_seconds: int = TIMEOUT_SECONDS) -> Dict[str, Any]:
    """Evaluate the program and return metrics."""
    start_time = time.time()

    try:
        u_set, c6_bound = run_program(program_path)
        eval_time = time.time() - start_time

        u_set = np.asarray(u_set, dtype=int)
        valid = validate_sums_diffs(u_set, c6_bound)
        target_ratio = BENCHMARK / float(c6_bound) if valid else 0.0
        validity = 1.0 if valid else 0.0
        combined_score = target_ratio * validity

        return {
            "c6_bound": float(c6_bound),
            "target_ratio": float(target_ratio),
            "validity": float(validity),
            "set_size": len(u_set),
            "max_val": int(np.max(u_set)),
            "eval_time": float(eval_time),
            "combined_score": float(combined_score),
        }

    except Exception as e:
        return {
            "c6_bound": 0.0,
            "target_ratio": 0.0,
            "validity": 0.0,
            "error": str(e),
            "traceback": traceback.format_exc(),
            "eval_time": time.time() - start_time,
        }


def is_successful(result: Dict[str, Any]) -> bool:
    if result.get("traceback") or result.get("error"):
        return False
    return result.get("combined_score", 0) > 0


def format_result(result: Dict[str, Any], artifact_ref: str) -> Dict[str, Any]:
    success = is_successful(result)
    status = "success" if success else "failure"
    fitness = result.get("combined_score", 0.0)

    metrics = {}
    for key in ["validity", "eval_time"]:
        if key in result:
            metrics[key] = result[key]
    exclude_keys = {"combined_score", "validity", "eval_time", "c6_bound", "set_size", "max_val", "target_ratio", "error", "traceback"}
    misc = {k: v for k, v in result.items() if k not in exclude_keys}

    return {"status": status, "artifact_ref": artifact_ref, "fitness": fitness, "metrics": metrics, "misc": misc}


def save_result(result: Dict[str, Any], output_path: str, artifact_ref: str) -> None:
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    formatted = format_result(result, artifact_ref)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(formatted, f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description="Sums and Differences of Finite Sets Evaluator")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--artifact-ref", required=True)
    parser.add_argument("--timeout", type=int, default=TIMEOUT_SECONDS)
    parser.add_argument("--out")
    args = parser.parse_args()

    if not os.path.exists(args.artifact_ref):
        print(f"Program file not found: {args.artifact_ref}")
        sys.exit(1)

    result = evaluate(args.artifact_ref, timeout_seconds=args.timeout)
    result["run_id"] = args.run_id
    result["task_id"] = args.task_id
    result["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")

    output_path = args.out or os.path.join(os.path.dirname(args.artifact_ref), "eval_result.json")
    save_result(result, output_path, args.artifact_ref)

    if is_successful(result):
        print(output_path)
        sys.exit(0)
    else:
        print(result.get("traceback", result.get("error", "Unknown error")))
        sys.exit(1)


if __name__ == "__main__":
    main()

# ===--------------------------------------------------------------------------------------===#
#
# This file implements the evaluator for the heilbronn problem for convex regions, with
# 14 points.
#
# ===--------------------------------------------------------------------------------------===#
#
# Some of the code in this file is adapted from:
#
# google-deepmind/alphaevolve_results:
# Licensed under the Apache License v2.0.
#
# ===--------------------------------------------------------------------------------------===#

import itertools
import os
import sys
import time
from importlib import __import__

import numpy as np
from scipy.spatial import ConvexHull

BENCHMARK = 0.027835571458482138
NUM_POINTS = 14


def triangle_area(p1: np.ndarray, p2: np.ndarray, p3: np.ndarray) -> float:
    """Calculates the area of a triangle given its vertices p1, p2, and p3."""
    return abs(p1[0] * (p2[1] - p3[1]) + p2[0] * (p3[1] - p1[1]) + p3[0] * (p1[1] - p2[1])) / 2


def evaluate(program_path: str):
    try:
        abs_program_path = os.path.abspath(program_path)
        program_dir = os.path.dirname(abs_program_path)
        module_name = os.path.splitext(os.path.basename(program_path))[0]

        points = None
        try:
            sys.path.insert(0, program_dir)
            program = __import__(module_name)

            start_time = time.time()
            points = program.heilbronn_convex14()
            end_time = time.time()
            eval_time = end_time - start_time
        except Exception as err:
            raise err
        finally:
            if program_dir in sys.path:
                sys.path.remove(program_dir)

        if not isinstance(points, np.ndarray):
            points = np.array(points)

        if points.shape != (NUM_POINTS, 2):
            raise ValueError(f"Invalid shapes: points = {points.shape}, expected {(NUM_POINTS,2)}")

        min_triangle_area = min(
            [triangle_area(p1, p2, p3) for p1, p2, p3 in itertools.combinations(points, 3)]
        )
        convex_hull_area = ConvexHull(points).volume
        min_area_normalized = min_triangle_area / convex_hull_area

        return {
            "min_area_normalized": float(min_area_normalized),
            "combined_score": float(min_area_normalized / BENCHMARK),
            "eval_time": float(eval_time),
        }
    except Exception as e:
        return {"combined_score": 0.0, "error": str(e)}
# =============================================================================
# Standalone EvoTrias CLI
# =============================================================================

import argparse
import json


def _is_successful(result):
    return not result.get("error") and not result.get("traceback")


def _format_result(result, artifact_ref):
    metric_keys = [
        key for key, value in result.items()
        if key != "combined_score" and isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    exclude_keys = set(metric_keys) | {"combined_score"}
    return {
        "status": "success" if _is_successful(result) else "failure",
        "artifact_ref": artifact_ref,
        "fitness": result.get("combined_score", 0.0),
        "metrics": {key: result[key] for key in metric_keys},
        "misc": {key: result[key] for key in result if key not in exclude_keys},
    }


def _main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--subagent-id", default="")
    parser.add_argument("--artifact-ref", required=True)
    parser.add_argument("--mode", default="full")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--out", help="Output JSON file path")
    args = parser.parse_args()

    if not os.path.exists(args.artifact_ref):
        print(f"Program file not found: {args.artifact_ref}")
        sys.exit(1)

    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        result = evaluate(args.artifact_ref)
    finally:
        sys.stdout = real_stdout
    result.update({
        "run_id": args.run_id,
        "task_id": args.task_id,
        "subagent_id": args.subagent_id,
        "mode": args.mode,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    })

    output_path = args.out or os.path.join(os.path.dirname(args.artifact_ref), "eval_result.json")
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(_format_result(result, args.artifact_ref), handle, indent=2, ensure_ascii=False)

    if _is_successful(result):
        print(output_path)
        sys.exit(0)
    print(result.get("error") or result.get("traceback") or "Unknown error")
    sys.exit(1)


if __name__ == "__main__":
    _main()


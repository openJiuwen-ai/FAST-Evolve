#!/usr/bin/env python
"""Heilbronn convex-region evaluator for 13 points."""

import argparse
import itertools
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Any, Dict

import numpy as np


BENCHMARK = 0.030936889034895654
NUM_POINTS = 13


def triangle_area(p1: np.ndarray, p2: np.ndarray, p3: np.ndarray) -> float:
    return abs(
        p1[0] * (p2[1] - p3[1])
        + p2[0] * (p3[1] - p1[1])
        + p3[0] * (p1[1] - p2[1])
    ) / 2


def convex_hull_area(points: np.ndarray) -> float:
    """Compute 2D convex hull area with Andrew's monotonic chain algorithm."""
    unique = sorted({(float(x), float(y)) for x, y in points})
    if len(unique) < 3:
        return 0.0

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)

    upper = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)

    hull = lower[:-1] + upper[:-1]
    if len(hull) < 3:
        return 0.0
    area = 0.0
    for index, point in enumerate(hull):
        next_point = hull[(index + 1) % len(hull)]
        area += point[0] * next_point[1] - next_point[0] * point[1]
    return abs(area) / 2


def run_program_with_timeout(program_path: str, timeout_seconds: int = 600):
    program_path_escaped = program_path.replace("\\", "/")
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as temp_file:
        temp_path_escaped = temp_file.name.replace("\\", "/")
        script = f"""
import importlib.util
import os
import pickle
import sys
import traceback

sys.path.insert(0, os.path.dirname('{program_path_escaped}'))
try:
    spec = importlib.util.spec_from_file_location("program", '{program_path_escaped}')
    program = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(program)
    points = program.heilbronn_convex13()
    with open('{temp_path_escaped}.results', 'wb') as f:
        pickle.dump({{'points': points}}, f)
except Exception as e:
    with open('{temp_path_escaped}.results', 'wb') as f:
        pickle.dump({{'error': str(e), 'traceback': traceback.format_exc()}}, f)
"""
        temp_file.write(script.encode())
        temp_file_path = temp_file.name
    results_path = f"{temp_file_path}.results"
    try:
        process = subprocess.Popen([sys.executable, temp_file_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            process.communicate(timeout=timeout_seconds)
            if os.path.exists(results_path):
                import pickle

                with open(results_path, "rb") as handle:
                    results = pickle.load(handle)
                if "error" in results:
                    raise RuntimeError(results["error"])
                return results["points"]
            raise RuntimeError("Program did not write evaluator results.")
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise TimeoutError(f"Execution timed out after {timeout_seconds} seconds")
    finally:
        for path in (temp_file_path, results_path):
            if os.path.exists(path):
                os.unlink(path)


def evaluate(program_path: str, timeout_seconds: int = 600) -> Dict[str, Any]:
    start_time = time.time()
    try:
        points = run_program_with_timeout(program_path, timeout_seconds)
        eval_time = time.time() - start_time
        if not isinstance(points, np.ndarray):
            points = np.array(points)
        valid = bool(points.shape == (NUM_POINTS, 2) and np.isfinite(points).all())
        min_area_normalized = 0.0
        if valid:
            min_triangle_area = min(
                triangle_area(p1, p2, p3)
                for p1, p2, p3 in itertools.combinations(points, 3)
            )
            hull_area = convex_hull_area(points)
            min_area_normalized = float(min_triangle_area / hull_area) if hull_area > 0 else 0.0
        target_ratio = min_area_normalized / BENCHMARK if valid else 0.0
        return {
            "min_area_normalized": float(min_area_normalized),
            "target_ratio": float(target_ratio),
            "combined_score": float(target_ratio if valid else 0.0),
            "eval_time": float(eval_time),
            "validity": 1.0 if valid else 0.0,
        }
    except Exception as exc:
        return {
            "min_area_normalized": 0.0,
            "target_ratio": 0.0,
            "combined_score": 0.0,
            "eval_time": float(time.time() - start_time),
            "validity": 0.0,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }


def is_successful(result: Dict[str, Any]) -> bool:
    return not result.get("traceback") and not result.get("error") and result.get("validity", 0) >= 1.0


def format_result(result: Dict[str, Any], artifact_ref: str) -> Dict[str, Any]:
    metric_keys = ["min_area_normalized", "target_ratio", "validity", "eval_time"]
    exclude = {"combined_score", *metric_keys}
    return {
        "status": "success" if is_successful(result) else "failure",
        "artifact_ref": artifact_ref,
        "fitness": result.get("combined_score", 0.0),
        "metrics": {key: result[key] for key in metric_keys if key in result},
        "misc": {key: result[key] for key in result if key not in exclude},
    }


def main() -> None:
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
    result = evaluate(args.artifact_ref, args.timeout)
    result.update({"run_id": args.run_id, "task_id": args.task_id, "subagent_id": args.subagent_id, "mode": args.mode, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")})
    output_path = args.out or os.path.join(os.path.dirname(args.artifact_ref), "eval_result.json")
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(format_result(result, args.artifact_ref), handle, indent=2)
    if is_successful(result):
        print(output_path)
        sys.exit(0)
    print(result.get("traceback") or result.get("error") or "Unknown error")
    sys.exit(1)


if __name__ == "__main__":
    main()

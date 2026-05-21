#!/usr/bin/env python
"""Standalone evaluator for 2D minimizing max-min distance benchmark."""

import argparse
import json
import os
import pickle
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Any, Dict

import numpy as np


NUM_POINTS = 16
DIMENSION = 2
BENCHMARK = 1 / 12.889266112


def run_program_with_timeout(program_path: str, timeout_seconds: int = 600):
    program_path_escaped = program_path.replace("\\", "/")
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as temp_file:
        temp_path_escaped = temp_file.name.replace("\\", "/")
        script = f"""
import os
import pickle
import sys
import traceback

sys.path.insert(0, os.path.dirname('{program_path_escaped}'))
try:
    spec = __import__('importlib.util').util.spec_from_file_location("program", '{program_path_escaped}')
    program = __import__('importlib.util').util.module_from_spec(spec)
    spec.loader.exec_module(program)
    points = program.min_max_dist_dim2_16()
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
            if not os.path.exists(results_path):
                raise RuntimeError("Results file not found")
            with open(results_path, "rb") as handle:
                results = pickle.load(handle)
            if "error" in results:
                raise RuntimeError(results["error"])
            return results["points"]
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise TimeoutError(f"Execution timed out after {timeout_seconds} seconds")
    finally:
        for path in (temp_file_path, results_path):
            if os.path.exists(path):
                os.unlink(path)


def min_max_ratio(points: np.ndarray) -> float:
    distances = []
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            distances.append(float(np.linalg.norm(points[i] - points[j])))
    if not distances:
        return 0.0
    dmin = min(distances)
    dmax = max(distances)
    return float((dmin / dmax) ** 2) if dmax > 0 else 0.0


def evaluate(program_path: str, timeout_seconds: int = 600) -> Dict[str, Any]:
    start_time = time.time()
    try:
        points = run_program_with_timeout(program_path, timeout_seconds)
        eval_time = time.time() - start_time
        if not isinstance(points, np.ndarray):
            points = np.array(points)
        valid = bool(points.shape == (NUM_POINTS, DIMENSION) and np.isfinite(points).all())
        value = min_max_ratio(points) if valid else 0.0
        target_ratio = value / BENCHMARK if valid else 0.0
        return {
            "min_max_ratio": float(value),
            "target_ratio": float(target_ratio),
            "validity": 1.0 if valid else 0.0,
            "eval_time": float(eval_time),
            "combined_score": float(target_ratio if valid else 0.0),
        }
    except Exception as exc:
        return {"min_max_ratio": 0.0, "target_ratio": 0.0, "validity": 0.0, "eval_time": float(time.time() - start_time), "combined_score": 0.0, "error": str(exc), "traceback": traceback.format_exc()}


def is_successful(result: Dict[str, Any]) -> bool:
    return not result.get("traceback") and not result.get("error") and result.get("validity", 0) >= 1.0


def format_result(result: Dict[str, Any], artifact_ref: str) -> Dict[str, Any]:
    metric_keys = ["min_max_ratio", "target_ratio", "validity", "eval_time"]
    exclude_keys = {"combined_score", *metric_keys}
    return {"status": "success" if is_successful(result) else "failure", "artifact_ref": artifact_ref, "fitness": result.get("combined_score", 0.0), "metrics": {key: result[key] for key in metric_keys if key in result}, "misc": {key: result[key] for key in result if key not in exclude_keys}}


def main() -> None:
    parser = argparse.ArgumentParser(description="Minimizing Max-Min Distance 2D Evaluator")
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
    result = evaluate(args.artifact_ref, timeout_seconds=args.timeout)
    result.update({"run_id": args.run_id, "task_id": args.task_id, "subagent_id": args.subagent_id, "mode": args.mode, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")})
    output_path = args.out or os.path.join(os.path.dirname(args.artifact_ref), "eval_result.json")
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(format_result(result, args.artifact_ref), handle, indent=2, ensure_ascii=False)
    if is_successful(result):
        print(output_path)
        sys.exit(0)
    print(result.get("traceback") or result.get("error") or "Validity check failed")
    sys.exit(1)


if __name__ == "__main__":
    main()

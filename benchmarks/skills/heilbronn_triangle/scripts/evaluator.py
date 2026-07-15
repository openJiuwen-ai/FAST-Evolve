#!/usr/bin/env python
"""
Heilbronn Triangle Evaluator (11 points)

Usage:
    python evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref>
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Any, Dict
import numpy as np
import itertools

BENCHMARK = 0.036529889880030156
NUM_POINTS = 11
TOL = 1e-6


def validate_heilbronn(points: np.ndarray) -> bool:
    if np.isnan(points).any():
        return False
    if points.shape != (NUM_POINTS, 2):
        return False
    for x, y in points:
        cond1 = y >= -TOL
        cond2 = np.sqrt(3) * x <= np.sqrt(3) - y + TOL
        cond3 = y <= np.sqrt(3) * x + TOL
        if not (cond1 and cond2 and cond3):
            return False
    return True


def triangle_area(a, b, c):
    return np.abs(a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1])) / 2


def run_program_with_timeout(program_path: str, timeout_seconds: int = 600):
    program_path_escaped = program_path.replace('\\', '/')
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as temp_file:
        temp_path_escaped = temp_file.name.replace('\\', '/')
        script = f"""
import sys, numpy as np, os, traceback
sys.path.insert(0, os.path.dirname('{program_path_escaped}'))
try:
    spec = __import__('importlib.util').util.spec_from_file_location("program", '{program_path_escaped}')
    program = __import__('importlib.util').util.module_from_spec(spec)
    spec.loader.exec_module(program)
    points = program.heilbronn_triangle11()
    import pickle
    with open('{temp_path_escaped}.results', 'wb') as f:
        pickle.dump({{'points': points}}, f)
except Exception as e:
    import pickle
    with open('{temp_path_escaped}.results', 'wb') as f:
        pickle.dump({{'error': str(e), 'traceback': traceback.format_exc()}}, f)
"""
        temp_file.write(script.encode())
        temp_file_path = temp_file.name
    results_path = f"{temp_file_path}.results"
    try:
        process = subprocess.Popen([sys.executable, temp_file_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
            if os.path.exists(results_path):
                import pickle
                with open(results_path, "rb") as f:
                    results = pickle.load(f)
                if "error" in results:
                    raise RuntimeError(results["error"])
                return results["points"]
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise TimeoutError(f"Execution timed out after {timeout_seconds} seconds")
    finally:
        for p in [temp_file_path, results_path]:
            if os.path.exists(p):
                os.unlink(p)


def evaluate(program_path: str, timeout_seconds: int = 600) -> Dict[str, Any]:
    start_time = time.time()
    try:
        points = run_program_with_timeout(program_path, timeout_seconds=timeout_seconds)
        eval_time = time.time() - start_time
        if not isinstance(points, np.ndarray):
            points = np.array(points)

        valid = validate_heilbronn(points)
        min_area_normalized = 0.0
        if valid:
            a, b, c = np.array([0, 0]), np.array([1, 0]), np.array([0.5, np.sqrt(3) / 2])
            min_triangle_area = min([triangle_area(p1, p2, p3) for p1, p2, p3 in itertools.combinations(points, 3)])
            min_area_normalized = min_triangle_area / triangle_area(a, b, c)

        target_ratio = min_area_normalized / BENCHMARK if valid else 0.0
        validity = 1.0 if valid else 0.0
        combined_score = target_ratio * validity

        return {
            "min_area_normalized": float(min_area_normalized),
            "target_ratio": float(target_ratio),
            "combined_score": float(combined_score),
            "eval_time": float(eval_time),
            "validity": float(validity),
        }
    except Exception as e:
        return {"min_area_normalized": 0.0, "target_ratio": 0.0, "combined_score": 0.0, "eval_time": float(time.time() - start_time), "validity": 0.0, "error": str(e), "traceback": traceback.format_exc()}


def is_successful(r): return not r.get("traceback") and not r.get("error") and r.get("validity", 0) >= 1.0
def get_error_message(r): return r.get("traceback") or r.get("error") or "Unknown error"


def format_result(result: Dict[str, Any], artifact_ref: str) -> Dict[str, Any]:
    metric_keys = ["validity", "eval_time", "min_area_normalized", "target_ratio"]
    metrics = {}
    for key in metric_keys:
        if key in result:
            metrics[key] = result[key]
    profile = {key: result[key] for key in ["validity", "eval_time"] if key in result}
    exclude_keys = {"combined_score", *metric_keys}
    return {
        "status": "success" if is_successful(result) else "failure",
        "artifact_ref": artifact_ref,
        "fitness": result.get("combined_score", 0.0),
        "metrics": metrics,
        "profile": profile,
        "misc": {k: result[k] for k in result if k not in exclude_keys},
    }


def main():
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
    result = evaluate(args.artifact_ref, timeout_seconds=args.timeout)
    result.update({"run_id": args.run_id, "task_id": args.task_id, "subagent_id": args.subagent_id, "mode": args.mode, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")})
    output_path = args.out or os.path.join(os.path.dirname(args.artifact_ref), "eval_result.json")
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(format_result(result, args.artifact_ref), f, indent=2)
    if is_successful(result):
        print(output_path)
        sys.exit(0)
    else:
        print(get_error_message(result))
        sys.exit(1)


if __name__ == "__main__":
    main()

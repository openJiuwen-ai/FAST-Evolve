#!/usr/bin/env python
"""
Minimizing Max-Min Distance Evaluator (2D, 16 points)

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
import scipy.spatial

NUM_POINTS = 16
DIMENSION = 2
BENCHMARK = 1 / 12.889266112


def validate_points(points: np.ndarray) -> bool:
    if np.isnan(points).any():
        return False
    if points.shape != (NUM_POINTS, DIMENSION):
        return False
    return True


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
    points = program.min_max_dist_dim2_16()
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

        valid = validate_points(points)
        min_max_ratio = 0.0
        if valid:
            pairwise_distances = scipy.spatial.distance.pdist(points)
            min_distance = np.min(pairwise_distances)
            max_distance = np.max(pairwise_distances)
            min_max_ratio = (min_distance / max_distance) ** 2 if max_distance > 0 else 0

        target_ratio = min_max_ratio / BENCHMARK if valid else 0.0
        validity = 1.0 if valid else 0.0
        combined_score = target_ratio * validity

        return {
            "min_max_ratio": float(min_max_ratio),
            "target_ratio": float(target_ratio),
            "combined_score": float(combined_score),
            "eval_time": float(eval_time),
            "validity": float(validity),
        }
    except Exception as e:
        return {"min_max_ratio": 0.0, "target_ratio": 0.0, "combined_score": 0.0, "eval_time": float(time.time() - start_time), "validity": 0.0, "error": str(e), "traceback": traceback.format_exc()}


def is_successful(r): return not r.get("traceback") and not r.get("error") and r.get("validity", 0) >= 1.0


def format_result(result: Dict[str, Any], artifact_ref: str) -> Dict[str, Any]:
    metrics = {}
    for key in ["validity", "eval_time"]:
        if key in result:
            metrics[key] = result[key]
    exclude_keys = {"combined_score", "validity", "eval_time", "min_max_ratio", "target_ratio"}
    return {
        "status": "success" if is_successful(result) else "failure",
        "artifact_ref": artifact_ref,
        "fitness": result.get("combined_score", 0.0),
        "metrics": metrics,
        "misc": {k: result[k] for k in result if k not in exclude_keys},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--subagent-id", default="")
    parser.add_argument("--artifact-ref", required=True)
    parser.add_argument("--mode", default="full")
    parser.add_argument("--timeout", type=int, default=360)
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
        print(result.get("traceback") or result.get("error") or "Unknown error")
        sys.exit(1)


if __name__ == "__main__":
    main()

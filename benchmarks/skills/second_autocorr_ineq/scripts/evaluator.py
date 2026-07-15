#!/usr/bin/env python
"""
Second Autocorrelation Inequality Evaluator

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

BENCHMARK = 0.8962799441554086


def validate_c2(f_values, c2_achieved, n_points):
    """Validate the C2 solution. Returns True if valid, False otherwise."""
    if np.isnan(f_values).any() or np.isnan(c2_achieved):
        return False
    if f_values.shape != (n_points,):
        return False
    if np.any(f_values < -1e-6):
        return False
    f_nonneg = np.maximum(f_values, 0.0)
    convolution = np.convolve(f_nonneg, f_nonneg, mode="full")
    num_conv_points = len(convolution)
    x_points = np.linspace(-0.5, 0.5, num_conv_points + 2)
    x_intervals = np.diff(x_points)
    y_points = np.concatenate(([0], convolution, [0]))
    l2_norm_squared = 0.0
    for i in range(len(convolution) + 1):
        y1, y2, h = y_points[i], y_points[i + 1], x_intervals[i]
        interval_l2_squared = (h / 3) * (y1**2 + y1 * y2 + y2**2)
        l2_norm_squared += interval_l2_squared
    norm_1 = np.sum(np.abs(convolution)) / (len(convolution) + 1)
    norm_inf = np.max(np.abs(convolution))
    if norm_1 < 1e-10 or norm_inf < 1e-10:
        return False
    computed_c2 = l2_norm_squared / (norm_1 * norm_inf)
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
    f_values, c2_achieved_from_opt, loss, n_points = program.run()
    import pickle
    with open('{temp_path_escaped}.results', 'wb') as f:
        pickle.dump({{'f_values': f_values, 'c2_achieved': c2_achieved_from_opt, 'loss': loss, 'n_points': n_points}}, f)
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
                return results["f_values"], results["c2_achieved"], results["loss"], results["n_points"]
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
        f_values, c2_achieved, loss, n_points = run_program_with_timeout(program_path, timeout_seconds=timeout_seconds)
        eval_time = time.time() - start_time
        valid = validate_c2(f_values, c2_achieved, n_points)
        target_ratio = BENCHMARK / float(c2_achieved) if valid else 0.0
        validity = 1.0 if valid else 0.0
        combined_score = target_ratio * validity
        return {"c2": float(c2_achieved), "target_ratio": float(target_ratio), "validity": float(validity), "loss": float(loss), "n_points": int(n_points), "eval_time": float(eval_time), "combined_score": float(combined_score)}
    except Exception as e:
        return {"c2": 0.0, "target_ratio": 0.0, "validity": 0.0, "loss": 0.0, "n_points": 0, "eval_time": float(time.time() - start_time), "validity": 0.0, "error": str(e), "traceback": traceback.format_exc()}


def is_successful(r): return not r.get("traceback") and not r.get("error") and r.get("validity", 0) >= 1.0


def format_result(result: Dict[str, Any], artifact_ref: str) -> Dict[str, Any]:
    success = is_successful(result)
    status = "success" if success else "failure"
    fitness = result.get("combined_score", 0.0)

    metric_keys = ["validity", "eval_time", "c2", "loss", "n_points", "target_ratio"]
    metrics = {}
    for key in metric_keys:
        if key in result:
            metrics[key] = result[key]
    profile = {key: result[key] for key in ["validity", "eval_time"] if key in result}
    exclude_keys = {"combined_score", *metric_keys}
    misc = {k: v for k, v in result.items() if k not in exclude_keys}

    return {"status": status, "artifact_ref": artifact_ref, "fitness": fitness, "metrics": metrics, "profile": profile, "misc": misc}


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
    formatted = format_result(result, args.artifact_ref)
    with open(output_path, "w") as f:
        json.dump(formatted, f, indent=2)
    if is_successful(result):
        print(output_path)
        sys.exit(0)
    else:
        print(result.get("traceback") or result.get("error") or "Unknown error")
        sys.exit(1)


if __name__ == "__main__":
    main()

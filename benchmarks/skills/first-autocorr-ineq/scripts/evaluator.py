#!/usr/bin/env python
"""
First Autocorrelation Inequality Evaluator

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

BENCHMARK = 1.5052939684401607  # Lower is better


def validate_autocorr(f_values, c1_achieved, n_points):
    """Validate the autocorrelation solution. Returns True if valid, False otherwise."""
    if np.isnan(f_values).any() or np.isnan(c1_achieved):
        return False
    if f_values.shape != (n_points,):
        return False
    if np.any(f_values < 0.0):
        return False
    dx = 0.5 / n_points
    f_nonneg = np.maximum(f_values, 0.0)
    autoconv = np.convolve(f_nonneg, f_nonneg, mode="full") * dx
    integral_sq = (np.sum(f_nonneg) * dx) ** 2
    if integral_sq < 1e-8:
        return False
    computed_c1 = float(np.max(autoconv / integral_sq))
    delta = abs(computed_c1 - c1_achieved)
    if delta > 1e-6:
        return False
    return True


def run_program_with_timeout(program_path: str, timeout_seconds: int = 600):
    """Run the program in a subprocess with timeout."""
    program_path_escaped = program_path.replace('\\', '/')

    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as temp_file:
        temp_path_escaped = temp_file.name.replace('\\', '/')
        script = f"""
import sys
import numpy as np
import os
import traceback

sys.path.insert(0, os.path.dirname('{program_path_escaped}'))

try:
    spec = __import__('importlib.util').util.spec_from_file_location("program", '{program_path_escaped}')
    program = __import__('importlib.util').util.module_from_spec(spec)
    spec.loader.exec_module(program)

    f_values, c1_achieved, loss, n_points = program.run()

    results = {{'f_values': f_values, 'c1_achieved': c1_achieved, 'loss': loss, 'n_points': n_points}}

    import pickle
    with open('{temp_path_escaped}.results', 'wb') as f:
        pickle.dump(results, f)

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
            exit_code = process.returncode

            if exit_code != 0:
                error_msg = stderr.decode() if stderr else f"Process exited with code {exit_code}"
                raise RuntimeError(error_msg)

            if os.path.exists(results_path):
                import pickle
                with open(results_path, "rb") as f:
                    results = pickle.load(f)

                if "error" in results:
                    raise RuntimeError(results["error"])

                return results["f_values"], results["c1_achieved"], results["loss"], results["n_points"]
            else:
                raise RuntimeError("Results file not found")

        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise TimeoutError(f"Execution timed out after {timeout_seconds} seconds")

    finally:
        if os.path.exists(temp_file_path):
            os.unlink(temp_file_path)
        if os.path.exists(results_path):
            os.unlink(results_path)


def evaluate(program_path: str, timeout_seconds: int = 600) -> Dict[str, Any]:
    """Evaluate the program and return metrics."""
    start_time = time.time()

    try:
        f_values, c1_achieved, loss, n_points = run_program_with_timeout(program_path, timeout_seconds=timeout_seconds)
        eval_time = time.time() - start_time

        valid = validate_autocorr(f_values, c1_achieved, n_points)
        target_ratio = BENCHMARK / float(c1_achieved) if valid else 0.0
        validity = 1.0 if valid else 0.0
        combined_score = target_ratio * validity

        return {
            "c1": float(c1_achieved),
            "target_ratio": float(target_ratio),
            "validity": float(validity),
            "loss": float(loss),
            "n_points": int(n_points),
            "eval_time": float(eval_time),
            "combined_score": float(combined_score),
        }

    except Exception as e:
        return {
            "c1": 0.0,
            "target_ratio": 0.0,
            "validity": 0.0,
            "loss": 0.0,
            "n_points": 0,
            "eval_time": float(time.time() - start_time),
            "validity": 0.0,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }


def is_successful(result: Dict[str, Any]) -> bool:
    if result.get("traceback") or result.get("error"):
        return False
    validity = result.get("validity")
    return validity is not None and validity >= 1.0


def get_error_message(result: Dict[str, Any]) -> str:
    if result.get("traceback"):
        return result["traceback"]
    if result.get("error"):
        return str(result["error"])
    return "Unknown error"


def format_result(result: Dict[str, Any], artifact_ref: str) -> Dict[str, Any]:
    success = is_successful(result)
    status = "success" if success else "failure"
    fitness = result.get("combined_score", 0.0)

    metric_keys = ["validity", "eval_time", "c1", "loss", "n_points", "target_ratio"]
    metrics = {}
    for key in metric_keys:
        if key in result:
            metrics[key] = result[key]
    profile = {key: result[key] for key in ["validity", "eval_time"] if key in result}
    exclude_keys = {"combined_score", *metric_keys}
    misc = {k: v for k, v in result.items() if k not in exclude_keys}

    return {"status": status, "artifact_ref": artifact_ref, "fitness": fitness, "metrics": metrics, "profile": profile, "misc": misc}


def save_result(result: Dict[str, Any], output_path: str, artifact_ref: str) -> None:
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    formatted = format_result(result, artifact_ref)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(formatted, f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description="First Autocorrelation Inequality Evaluator")
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
    save_result(result, output_path, args.artifact_ref)

    if is_successful(result):
        print(output_path)
        sys.exit(0)
    else:
        print(get_error_message(result))
        sys.exit(1)


if __name__ == "__main__":
    main()

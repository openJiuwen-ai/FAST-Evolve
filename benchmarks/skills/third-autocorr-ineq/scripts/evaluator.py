#!/usr/bin/env python
"""
Third Autocorrelation Inequality Evaluator

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
from typing import Any

import numpy as np

BENCHMARK = 1.4556427953745406  # Lower is better


def validate_c3(f_values, c3_achieved, n_points) -> tuple[bool, str, float]:
    """Validate the C3 upper-bound solution."""
    f_values = np.asarray(f_values)
    if f_values.shape != (n_points,):
        return False, f"Expected f_values shape {(n_points,)}, got {f_values.shape}", 0.0
    if not np.all(np.isfinite(f_values)):
        return False, "f_values contains NaN or infinite values", 0.0
    if not np.isfinite(c3_achieved):
        return False, f"c3_achieved is not finite: {c3_achieved}", 0.0

    dx = 0.5 / n_points
    integral_sq = (np.sum(f_values) * dx) ** 2
    if integral_sq < 1e-9:
        return False, "Function integral is close to zero, ratio is unstable", 0.0

    conv = np.convolve(f_values, f_values, mode="full")
    scaled_conv = conv * dx
    computed_c3 = float(np.max(np.abs(scaled_conv)) / integral_sq)

    delta = abs(computed_c3 - float(c3_achieved))
    if delta > 1e-3:
        return (
            False,
            f"C3 mismatch: reported {float(c3_achieved):.12g}, computed {computed_c3:.12g}, delta {delta:.12g}",
            computed_c3,
        )
    return True, "", computed_c3


def run_program_with_timeout(program_path: str, timeout_seconds: int = 600):
    """Run the program in a subprocess with timeout."""
    program_path_escaped = program_path.replace("\\", "/")

    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as temp_file:
        temp_path_escaped = temp_file.name.replace("\\", "/")
        script = f"""
import sys
import os
import traceback

sys.path.insert(0, os.path.dirname('{program_path_escaped}'))

try:
    spec = __import__('importlib.util').util.spec_from_file_location("program", '{program_path_escaped}')
    program = __import__('importlib.util').util.module_from_spec(spec)
    spec.loader.exec_module(program)

    run_result = program.run()
    if isinstance(run_result, dict):
        required_keys = ("f_values", "c3_achieved", "loss", "n_points")
        missing = [key for key in required_keys if key not in run_result]
        if missing:
            raise ValueError("run() returned a dict missing keys: " + str(missing))
        f_values = run_result["f_values"]
        c3_achieved = run_result["c3_achieved"]
        loss = run_result["loss"]
        n_points = run_result["n_points"]
    else:
        try:
            f_values, c3_achieved, loss, n_points = run_result
        except Exception as exc:
            raise ValueError(
                "run() must return (f_values, c3_achieved, loss, n_points) "
                "or a dict with those keys"
            ) from exc

    import pickle
    with open('{temp_path_escaped}.results', 'wb') as f:
        pickle.dump({{'f_values': f_values, 'c3_achieved': c3_achieved, 'loss': loss, 'n_points': n_points}}, f)

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
                    raise RuntimeError(f"{results['error']}\n{results.get('traceback', '')}")

                return results["f_values"], results["c3_achieved"], results["loss"], results["n_points"]
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


def evaluate(program_path: str, timeout_seconds: int = 600) -> dict[str, Any]:
    """Evaluate the program and return metrics."""
    start_time = time.time()

    try:
        f_values, c3_achieved, loss, n_points = run_program_with_timeout(program_path, timeout_seconds=timeout_seconds)
        eval_time = time.time() - start_time

        valid, validation_error, computed_c3 = validate_c3(f_values, c3_achieved, n_points)
        target_ratio = BENCHMARK / float(c3_achieved) if valid else 0.0
        validity = 1.0 if valid else 0.0
        combined_score = target_ratio * validity

        result = {
            "c3": float(c3_achieved),
            "computed_c3": float(computed_c3),
            "target_ratio": float(target_ratio),
            "validity": float(validity),
            "loss": float(loss),
            "n_points": int(n_points),
            "eval_time": float(eval_time),
            "combined_score": float(combined_score),
        }
        if validation_error:
            result["error"] = validation_error
        return result

    except Exception as e:
        return {
            "c3": 0.0,
            "target_ratio": 0.0,
            "validity": 0.0,
            "loss": 0.0,
            "n_points": 0,
            "eval_time": float(time.time() - start_time),
            "error": str(e),
            "traceback": traceback.format_exc(),
        }


def is_successful(result: dict[str, Any]) -> bool:
    if result.get("traceback") or result.get("error"):
        return False
    validity = result.get("validity")
    return validity is not None and validity >= 1.0


def get_error_message(result: dict[str, Any]) -> str:
    if result.get("traceback"):
        return result["traceback"]
    if result.get("error"):
        return str(result["error"])
    return "Unknown error"


def format_result(result: dict[str, Any], artifact_ref: str) -> dict[str, Any]:
    success = is_successful(result)
    status = "success" if success else "failure"
    fitness = result.get("combined_score", 0.0)

    metric_keys = [
        "validity",
        "eval_time",
        "c3",
        "computed_c3",
        "loss",
        "n_points",
        "target_ratio",
    ]
    metrics = {}
    for key in metric_keys:
        if key in result:
            metrics[key] = result[key]
    profile = {key: result[key] for key in ["validity", "eval_time"] if key in result}
    exclude_keys = {"combined_score", *metric_keys}
    misc = {k: v for k, v in result.items() if k not in exclude_keys}

    return {"status": status, "artifact_ref": artifact_ref, "fitness": fitness, "metrics": metrics, "profile": profile, "misc": misc}


def save_result(result: dict[str, Any], output_path: str, artifact_ref: str) -> None:
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    formatted = format_result(result, artifact_ref)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(formatted, f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description="Third Autocorrelation Inequality Evaluator")
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
    result.update(
        {
            "run_id": args.run_id,
            "task_id": args.task_id,
            "subagent_id": args.subagent_id,
            "mode": args.mode,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    )

    output_path = args.out or os.path.join(os.path.dirname(args.artifact_ref), "eval_result.json")
    save_result(result, output_path, args.artifact_ref)

    if is_successful(result):
        print(output_path)
        sys.exit(0)
    print(get_error_message(result))
    sys.exit(1)


if __name__ == "__main__":
    main()

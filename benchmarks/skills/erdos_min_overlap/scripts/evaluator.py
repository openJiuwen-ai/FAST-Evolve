#!/usr/bin/env python
"""
Erdős Minimum Overlap Evaluator

Usage:
    python evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref>

Output:
    - Success: prints result file path, exit code 0
    - Failure: prints error message, exit code 1
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

# Benchmark from AlphaEvolve (lower is better - we want to minimize C5)
BENCHMARK = 0.38092303510845016
TARGET_VALUE = BENCHMARK


def validate_erdos(h_values, c5_bound):
    """Validate erdos solution. Returns True if valid, False otherwise."""
    if np.isnan(h_values).any() or np.isnan(c5_bound):
        return False
    if np.isinf(h_values).any() or np.isinf(c5_bound):
        return False
    return True


def verify_c5_solution(h_values: np.ndarray, c5_achieved: float, n_points: int):
    """Verifies the C5 upper bound solution."""
    if h_values.shape != (n_points,):
        raise ValueError(f"Expected h shape ({n_points},), got {h_values.shape}")

    # Verify h(x) in [0, 1] constraint
    if np.any(h_values < 0) or np.any(h_values > 1):
        raise ValueError(f"h(x) is not in [0, 1]. Range: [{h_values.min()}, {h_values.max()}]")

    # Verify integral of h = 1 constraint
    dx = 2.0 / n_points
    integral_h = np.sum(h_values) * dx
    if not np.isclose(integral_h, 1.0, atol=1e-3):
        raise ValueError(f"Integral of h is not close to 1. Got: {integral_h:.6f}")

    # Re-calculate the C5 bound using np.correlate
    j_values = 1.0 - h_values
    correlation = np.correlate(h_values, j_values, mode="full") * dx
    computed_c5 = np.max(correlation)

    # Check for consistency
    if not np.isclose(computed_c5, c5_achieved, atol=1e-4):
        raise ValueError(f"C5 mismatch: reported {c5_achieved:.6f}, computed {computed_c5:.6f}")


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

    h_values, c5_bound, n_points = program.run()

    results = {{'h_values': h_values, 'c5_bound': c5_bound, 'n_points': n_points}}

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
        process = subprocess.Popen(
            [sys.executable, temp_file_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

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

                return results["h_values"], results["c5_bound"], results["n_points"]
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
        h_values, c5_bound, n_points = run_program_with_timeout(program_path, timeout_seconds=timeout_seconds)
        eval_time = time.time() - start_time

        valid = validate_erdos(h_values, c5_bound)

        if valid:
            # Verify the solution
            verify_c5_solution(h_values, c5_bound, n_points)
        else:
            c5_bound = 0.0

        # For C5: lower is better, so we compute score as BENCHMARK / c5_achieved
        # This is a MINIMIZATION problem (lower C5 is better)
        # target_ratio = BENCHMARK / c5_bound when valid (ratio > 1 means we beat the target)
        target_ratio = float(BENCHMARK / c5_bound) if valid and c5_bound > 0 else 0.0
        validity = 1.0 if valid else 0.0
        combined_score = target_ratio * validity

        return {
            "c5_bound": float(c5_bound),
            "target_ratio": float(target_ratio),
            "combined_score": float(combined_score),
            "n_points": int(n_points),
            "validity": float(validity),
            "eval_time": float(eval_time),
        }

    except Exception as e:
        return {
            "c5_bound": 0.0,
            "target_ratio": 0.0,
            "combined_score": 0.0,
            "n_points": 0,
            "validity": 0.0,
            "eval_time": float(time.time() - start_time),
            "error": str(e),
            "traceback": traceback.format_exc(),
        }


# =============================================================================
# Output Formatting
# =============================================================================

def is_successful(result: Dict[str, Any]) -> bool:
    """Check if evaluation was successful."""
    if result.get("traceback"):
        return False
    if result.get("error"):
        return False
    validity = result.get("validity")
    if validity is not None and validity < 1.0:
        return False
    return True


def get_error_message(result: Dict[str, Any]) -> str:
    """Extract error message from result."""
    if result.get("traceback"):
        return result["traceback"]
    if result.get("error"):
        return str(result["error"])
    validity = result.get("validity")
    if validity is not None and validity < 1.0:
        return f"Validity check failed: validity={validity}"
    return "Unknown error"


def format_result(result: Dict[str, Any], artifact_ref: str) -> Dict[str, Any]:
    """Format result into status, fitness, metrics, misc."""
    success = is_successful(result)

    status = "success" if success else "failure"
    fitness = result.get("combined_score", 0.0)

    metrics = {}
    for key in ["validity", "eval_time"]:
        if key in result:
            metrics[key] = result[key]

    exclude_keys = {"combined_score", "validity", "eval_time", "c5_bound", "n_points", "target_ratio"}
    misc = {k: v for k, v in result.items() if k not in exclude_keys}

    return {
        "status": status,
        "artifact_ref": artifact_ref,
        "fitness": fitness,
        "metrics": metrics,
        "misc": misc,
    }


def save_result(result: Dict[str, Any], output_path: str, artifact_ref: str) -> None:
    """Save result to JSON file."""
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    formatted = format_result(result, artifact_ref)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(formatted, f, indent=2, ensure_ascii=False)


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Erdős Minimum Overlap Evaluator")
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument("--task-id", required=True, help="Task identifier")
    parser.add_argument("--subagent-id", default="", help="Subagent identifier")
    parser.add_argument("--artifact-ref", required=True, help="Program artifact path")
    parser.add_argument("--mode", default="full", help="Evaluation mode")
    parser.add_argument("--timeout", type=int, default=600, help="Timeout in seconds")
    parser.add_argument("--out", help="Output JSON file path")

    args = parser.parse_args()

    program_path = args.artifact_ref

    if not os.path.exists(program_path):
        print(f"Program file not found: {program_path}")
        sys.exit(1)

    # Run evaluation
    result = evaluate(program_path, timeout_seconds=args.timeout)
    result["run_id"] = args.run_id
    result["task_id"] = args.task_id
    result["subagent_id"] = args.subagent_id
    result["mode"] = args.mode
    result["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")

    # Determine output path
    output_path = args.out or os.path.join(os.path.dirname(program_path), "eval_result.json")

    # Save result
    save_result(result, output_path, program_path)

    # Output based on success/failure
    if is_successful(result):
        print(output_path)
        sys.exit(0)
    else:
        print(get_error_message(result))
        sys.exit(1)


if __name__ == "__main__":
    main()

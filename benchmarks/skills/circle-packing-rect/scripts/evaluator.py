#!/usr/bin/env python
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""
Circle Packing Rectangle Evaluator - Containerized version

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
from typing import Any

import numpy as np

# Benchmark from AlphaEvolve
TARGET_VALUE = 2.3658321334167627
NUM_CIRCLES = 21
PERIMETER = 4.0  # width + height = 2, so perimeter = 4
TOL = 1e-6


# =============================================================================
# Evaluation Logic (from original evaluator.py)
# =============================================================================

def minimum_circumscribing_rectangle(circles: np.ndarray):
    """Returns the width and height of the minimum circumscribing rectangle."""
    min_x = np.min(circles[:, 0] - circles[:, 2])
    max_x = np.max(circles[:, 0] + circles[:, 2])
    min_y = np.min(circles[:, 1] - circles[:, 2])
    max_y = np.max(circles[:, 1] + circles[:, 2])
    return max_x - min_x, max_y - min_y


def validate_packing(circles: np.ndarray, tol: float = TOL) -> bool:
    """Validate that circles don't overlap and are inside a rectangle of perimeter 4."""
    n = circles.shape[0]

    # Check for NaN values
    if np.isnan(circles).any():
        return False

    # Check if radii are positive
    for i in range(n):
        if circles[i, 2] <= 0:
            return False

    # Check if circles fit inside a rectangle of perimeter 4
    width, height = minimum_circumscribing_rectangle(circles)
    if width + height > (PERIMETER / 2 + tol):
        return False

    # Check for overlaps
    for i in range(n):
        for j in range(i + 1, n):
            dist = np.sqrt(np.sum((circles[i, :2] - circles[j, :2]) ** 2))
            if dist < circles[i, 2] + circles[j, 2] - tol:
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

    circles = program.circle_packing21()

    results = {{'circles': circles}}

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

                return results["circles"]
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


def evaluate(program_path: str, timeout_seconds: int = 600) -> dict[str, Any]:
    """Evaluate the program and return metrics."""
    start_time = time.time()

    try:
        circles = run_program_with_timeout(program_path, timeout_seconds=timeout_seconds)
        eval_time = time.time() - start_time

        # Ensure numpy array
        if not isinstance(circles, np.ndarray):
            circles = np.array(circles)

        # Validate shape
        shape_valid = circles.shape == (NUM_CIRCLES, 3)
        if not shape_valid:
            return {
                "radii_sum": 0.0,
                "target_ratio": 0.0,
                "validity": 0.0,
                "eval_time": float(eval_time),
                "combined_score": 0.0,
                "error": f"Invalid shape: expected (21, 3), got {circles.shape}",
            }

        # Validate
        valid = validate_packing(circles)

        radii_sum = np.sum(circles[:, -1]) if valid else 0.0
        target_ratio = radii_sum / TARGET_VALUE if valid else 0.0
        validity = 1.0 if valid else 0.0
        combined_score = target_ratio * validity

        return {
            "radii_sum": float(radii_sum),
            "target_ratio": float(target_ratio),
            "validity": float(validity),
            "eval_time": float(eval_time),
            "combined_score": float(combined_score),
        }

    except Exception as e:
        return {
            "radii_sum": 0.0,
            "target_ratio": 0.0,
            "validity": 0.0,
            "eval_time": float(time.time() - start_time),
            "combined_score": 0.0,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }


# =============================================================================
# Output Formatting
# =============================================================================

def is_successful(result: dict[str, Any]) -> bool:
    """Check if evaluation was successful."""
    if result.get("traceback"):
        return False
    if result.get("error"):
        return False
    validity = result.get("validity")
    if validity is not None and validity < 1.0:
        return False
    return True


def get_error_message(result: dict[str, Any]) -> str:
    """Extract error message from result."""
    if result.get("traceback"):
        return result["traceback"]
    if result.get("error"):
        return str(result["error"])
    validity = result.get("validity")
    if validity is not None and validity < 1.0:
        return f"Validity check failed: validity={validity}"
    return "Unknown error"


def format_result(result: dict[str, Any], artifact_ref: str) -> dict[str, Any]:
    """Format result into status, fitness, metrics, misc."""
    success = is_successful(result)

    # Status
    status = "success" if success else "failure"

    # Fitness - main optimization score
    fitness = result.get("combined_score", 0.0)

    # Metrics
    metric_keys = ["validity", "eval_time", "radii_sum", "target_ratio"]
    metrics = {}
    for key in metric_keys:
        if key in result:
            metrics[key] = result[key]

    profile = {key: result[key] for key in ["validity", "eval_time"] if key in result}

    # Misc - everything else
    exclude_keys = {"combined_score", *metric_keys}
    misc = {k: v for k, v in result.items() if k not in exclude_keys}

    return {
        "status": status,
        "artifact_ref": artifact_ref,
        "fitness": fitness,
        "metrics": metrics,
        "profile": profile,
        "misc": misc,
    }


def save_result(result: dict[str, Any], output_path: str, artifact_ref: str) -> None:
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
    parser = argparse.ArgumentParser(description="Circle Packing Rectangle Evaluator")
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

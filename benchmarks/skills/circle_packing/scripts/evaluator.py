#!/usr/bin/env python
"""
Circle Packing Evaluator - Standalone evaluation script

Usage:
    python evaluator.py --run_id <run_id> --task_id <task_id> --program_name <program_name>

Output:
    - Success: prints result file path, exit code 0
    - Failure: prints error message, exit code 1
"""

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

# Constants
TARGET_VALUE = 2.635  # AlphaEvolve result for n=26
OUTPUTS_DIR = "benchmarks/outputs"


# =============================================================================
# Evaluation Logic (from original evaluator.py)
# =============================================================================

def validate_packing(centers, radii):
    """Validate that circles don't overlap and are inside the unit square."""
    n = centers.shape[0]

    # Check for NaN values
    if np.isnan(centers).any() or np.isnan(radii).any():
        return False

    # Check if radii are nonnegative
    for i in range(n):
        if radii[i] < 0:
            return False

    # Check if circles are inside the unit square
    for i in range(n):
        x, y = centers[i]
        r = radii[i]
        if x - r < -1e-6 or x + r > 1 + 1e-6 or y - r < -1e-6 or y + r > 1 + 1e-6:
            return False

    # Check for overlaps
    for i in range(n):
        for j in range(i + 1, n):
            dist = np.sqrt(np.sum((centers[i] - centers[j]) ** 2))
            if dist < radii[i] + radii[j] - 1e-6:
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
import pickle
import traceback

sys.path.insert(0, os.path.dirname('{program_path_escaped}'))

try:
    spec = __import__('importlib.util').util.spec_from_file_location("program", '{program_path_escaped}')
    program = __import__('importlib.util').util.module_from_spec(spec)
    spec.loader.exec_module(program)

    centers, radii, sum_radii = program.run_packing()

    results = {{
        'centers': centers,
        'radii': radii,
        'sum_radii': sum_radii
    }}

    with open('{temp_path_escaped}.results', 'wb') as f:
        pickle.dump(results, f)

except Exception as e:
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
                with open(results_path, "rb") as f:
                    results = pickle.load(f)

                if "error" in results:
                    raise RuntimeError(results["error"])

                return results["centers"], results["radii"], results["sum_radii"]
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


def evaluate(program_path: str) -> Dict[str, Any]:
    """Evaluate the program and return metrics."""
    start_time = time.time()

    try:
        centers, radii, reported_sum = run_program_with_timeout(program_path, timeout_seconds=600)
        eval_time = time.time() - start_time

        # Ensure numpy arrays
        if not isinstance(centers, np.ndarray):
            centers = np.array(centers)
        if not isinstance(radii, np.ndarray):
            radii = np.array(radii)

        # Validate
        valid = validate_packing(centers, radii)
        shape_valid = centers.shape == (26, 2) and radii.shape == (26,)

        if not shape_valid:
            valid = False

        sum_radii = np.sum(radii) if valid else 0.0
        target_ratio = sum_radii / TARGET_VALUE if valid else 0.0
        validity = 1.0 if valid else 0.0
        combined_score = target_ratio * validity

        return {
            "sum_radii": float(sum_radii),
            "target_ratio": float(target_ratio),
            "validity": float(validity),
            "eval_time": float(eval_time),
            "combined_score": float(combined_score),
        }

    except Exception as e:
        return {
            "sum_radii": 0.0,
            "target_ratio": 0.0,
            "validity": 0.0,
            "eval_time": float(time.time() - start_time),
            "combined_score": 0.0,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }


# =============================================================================
# Output Formatting (from standalone_evaluator.py)
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


def format_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Format result into status, fitness, metrics, misc."""
    success = is_successful(result)

    # Status
    status = "success" if success else "failed"

    # Fitness - main optimization score
    fitness = result.get("combined_score", 0.0)

    # Metrics
    metrics = {}
    for key in ["validity", "eval_time"]:
        if key in result:
            metrics[key] = result[key]

    # Misc - everything else
    exclude_keys = {"combined_score", "validity", "eval_time", "sum_radii", "target_ratio"}
    misc = {k: v for k, v in result.items() if k not in exclude_keys}

    return {
        "status": status,
        "fitness": fitness,
        "metrics": metrics,
        "misc": misc,
    }


def save_result(result: Dict[str, Any], output_path: str) -> None:
    """Save result to JSON file."""
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    formatted = format_result(result)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(formatted, f, indent=2, ensure_ascii=False)


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Circle Packing Evaluator")
    parser.add_argument("--run_id", required=True, help="Run identifier")
    parser.add_argument("--task_id", required=True, help="Task identifier")
    parser.add_argument("--program_name", required=True, help="Program file name")
    parser.add_argument("--timeout", type=int, default=600, help="Timeout in seconds")
    parser.add_argument("--output", help="Output file path")

    args = parser.parse_args()

    # Build program path (assuming program is in outputs/<run_id>/<task_id>/)
    program_path = os.path.join(OUTPUTS_DIR, args.run_id, args.task_id, args.program_name)

    if not os.path.exists(program_path):
        print(f"Program file not found: {program_path}")
        sys.exit(1)

    # Run evaluation
    result = evaluate(program_path)
    result["run_id"] = args.run_id
    result["task_id"] = args.task_id
    result["program_name"] = args.program_name
    result["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")

    # Determine output path
    output_path = args.output or os.path.join(OUTPUTS_DIR, args.run_id, args.task_id, "eval_result.json")

    # Save result
    save_result(result, output_path)

    # Output based on success/failure
    if is_successful(result):
        print(output_path)
        sys.exit(0)
    else:
        print(get_error_message(result))
        sys.exit(1)


if __name__ == "__main__":
    main()

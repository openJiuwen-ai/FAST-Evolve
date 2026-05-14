#!/usr/bin/env python
"""
Circle Packing Evaluator - Standalone evaluation script

Usage:
    python evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref>

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

TARGET_VALUE = 2.635  # AlphaEvolve result for n=26
TOL = 1e-6


# =============================================================================
# Evaluation Logic (from original evaluator.py)
# =============================================================================

def validate_packing(centers, radii):
    """Validate that circles don't overlap and are inside the unit square."""
    return validate_packing_details(centers, radii)["valid"]


def validate_packing_details(centers, radii) -> Dict[str, Any]:
    """Validate circle packing and return structured failure details."""
    if centers.shape != (26, 2) or radii.shape != (26,):
        return _validation_failure(
            "invalid_shape",
            "Expected centers shape (26, 2) and radii shape (26,).",
            centers_shape=list(centers.shape),
            radii_shape=list(radii.shape),
        )

    n = centers.shape[0]

    # Check for NaN/Inf values
    if not np.isfinite(centers).all():
        index = np.argwhere(~np.isfinite(centers))[0]
        circle_index = int(index[0])
        return _validation_failure(
            "nan_or_inf",
            f"Circle {circle_index} center contains NaN or Inf.",
            circle_indices=[circle_index],
            center=_safe_vector(centers[circle_index]),
        )
    if not np.isfinite(radii).all():
        circle_index = int(np.argwhere(~np.isfinite(radii))[0][0])
        return _validation_failure(
            "nan_or_inf",
            f"Circle {circle_index} radius is NaN or Inf.",
            circle_indices=[circle_index],
            radius=_safe_float(radii[circle_index]),
        )

    # Check if radii are nonnegative
    for i in range(n):
        if radii[i] < 0:
            return _validation_failure(
                "negative_radius",
                f"Circle {i} has a negative radius.",
                circle_indices=[i],
                center=_safe_vector(centers[i]),
                radius=_safe_float(radii[i]),
            )

    # Check if circles are inside the unit square
    for i in range(n):
        x, y = centers[i]
        r = radii[i]
        margins = {
            "left": _safe_float(x - r),
            "right": _safe_float(1 - (x + r)),
            "bottom": _safe_float(y - r),
            "top": _safe_float(1 - (y + r)),
        }
        boundary_margin = min(margins.values())
        if boundary_margin < -TOL:
            side = min(margins, key=margins.get)
            return _validation_failure(
                "boundary_violation",
                f"Circle {i} crosses the unit square boundary on the {side} side.",
                circle_indices=[i],
                center=_safe_vector(centers[i]),
                radius=_safe_float(r),
                boundary_margin=boundary_margin,
                margins=margins,
                tolerance=TOL,
            )

    # Check for overlaps
    for i in range(n):
        for j in range(i + 1, n):
            dist = np.sqrt(np.sum((centers[i] - centers[j]) ** 2))
            required_dist = radii[i] + radii[j]
            overlap_amount = required_dist - dist
            if overlap_amount > TOL:
                return _validation_failure(
                    "overlap",
                    f"Circles {i} and {j} overlap.",
                    circle_indices=[i, j],
                    centers=[_safe_vector(centers[i]), _safe_vector(centers[j])],
                    radii=[_safe_float(radii[i]), _safe_float(radii[j])],
                    distance=_safe_float(dist),
                    required_distance=_safe_float(required_dist),
                    overlap_amount=_safe_float(overlap_amount),
                    tolerance=TOL,
                )

    return {"valid": True}


def _validation_failure(failure_type: str, message: str, **details: Any) -> Dict[str, Any]:
    return {
        "valid": False,
        "failure_type": failure_type,
        "message": message,
        **details,
    }


def _safe_float(value: Any) -> Any:
    numeric = float(value)
    if np.isfinite(numeric):
        return numeric
    return str(numeric)


def _safe_vector(values: Any) -> list:
    return [_safe_float(value) for value in values]


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


def evaluate(program_path: str, timeout_seconds: int = 600) -> Dict[str, Any]:
    """Evaluate the program and return metrics."""
    start_time = time.time()

    try:
        centers, radii, reported_sum = run_program_with_timeout(
            program_path,
            timeout_seconds=timeout_seconds,
        )
        eval_time = time.time() - start_time

        # Ensure numeric numpy arrays
        try:
            centers = np.asarray(centers, dtype=float)
            radii = np.asarray(radii, dtype=float)
        except (TypeError, ValueError) as exc:
            validation_error = _validation_failure(
                "invalid_shape",
                "Centers and radii must be numeric array-like values.",
                error=str(exc),
            )
            return {
                "sum_radii": 0.0,
                "target_ratio": 0.0,
                "validity": 0.0,
                "eval_time": float(time.time() - start_time),
                "combined_score": 0.0,
                "validation_error": validation_error,
            }

        # Validate
        validation = validate_packing_details(centers, radii)
        valid = bool(validation["valid"])

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
            **({} if valid else {"validation_error": validation}),
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
    if result.get("validation_error"):
        validation_error = result["validation_error"]
        return f"Validity check failed: {_format_validation_error(validation_error)}"
    validity = result.get("validity")
    if validity is not None and validity < 1.0:
        return f"Validity check failed: validity={validity}"
    return "Unknown error"


def _format_validation_error(error: Dict[str, Any]) -> str:
    """Return a compact one-line validation summary for subagent debugging."""
    parts = [f"type={error.get('failure_type', 'unknown')}"]
    if "circle_indices" in error:
        parts.append(f"circles={error['circle_indices']}")
    for key in [
        "center",
        "radius",
        "boundary_margin",
        "distance",
        "required_distance",
        "overlap_amount",
        "centers_shape",
        "radii_shape",
    ]:
        if key in error:
            parts.append(f"{key}={error[key]}")
    if error.get("message"):
        parts.append(f"message={error['message']}")
    return "; ".join(parts)


def format_result(result: Dict[str, Any], artifact_ref: str) -> Dict[str, Any]:
    """Format result into status, fitness, metrics, misc."""
    success = is_successful(result)

    # Status
    status = "success" if success else "failure"

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
    parser = argparse.ArgumentParser(description="Circle Packing Evaluator")
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

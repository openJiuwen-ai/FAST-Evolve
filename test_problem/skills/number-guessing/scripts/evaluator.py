#!/usr/bin/env python
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""
Number Guessing Evaluator - Standalone evaluation script

Usage:
    python verify.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref>

Output:
    - Success: prints result file path, exit code 0
    - Failure: prints error message, exit code 1
"""

import argparse
import json
import os
import sys
import time
import traceback
from typing import Any

# The secret number to guess - fixed for evaluation
SECRET_NUMBER = 35146
MIN_VALUE = 1
MAX_VALUE = 100000


def validate_guess(guess: int) -> dict[str, Any]:
    """Validate that the guess is within the valid range."""
    if not isinstance(guess, int):
        return {
            "valid": False,
            "failure_type": "invalid_type",
            "message": f"Expected integer, got {type(guess).__name__}.",
        }

    if guess < MIN_VALUE or guess > MAX_VALUE:
        return {
            "valid": False,
            "failure_type": "out_of_range",
            "message": f"Guess must be between {MIN_VALUE} and {MAX_VALUE}.",
        }

    return {"valid": True}


def read_guess_from_file(file_path: str) -> int:
    """Read guess from text file."""
    with open(file_path, encoding='utf-8') as f:
        content = f.read().strip()
    return int(content)


def evaluate(file_path: str) -> dict[str, Any]:
    """Evaluate the guess and return metrics."""
    _start_time = time.time()

    try:
        guess = read_guess_from_file(file_path)
        validation = validate_guess(guess)
        valid = bool(validation["valid"])

        if valid:
            distance = abs(guess - SECRET_NUMBER)
            max_possible_distance = max(SECRET_NUMBER - MIN_VALUE, MAX_VALUE - SECRET_NUMBER)
            closeness = 1.0 - (distance / max_possible_distance)
        else:
            closeness = 0.0

        return {
            "validity": 1.0 if valid else 0.0,
            "combined_score": float(closeness),
            **({} if valid else {"validation_error": validation}),
        }

    except Exception as e:
        return {
            "validity": 0.0,
            "combined_score": 0.0,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }


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
    if result.get("validation_error"):
        return f"Validity check failed: {result['validation_error'].get('message', 'unknown')}"
    return "Unknown error"


def format_result(result: dict[str, Any], artifact_ref: str) -> dict[str, Any]:
    """Format result into status, fitness, metrics, misc."""
    success = is_successful(result)
    return {
        "status": "success" if success else "failure",
        "artifact_ref": artifact_ref,
        "fitness": result.get("combined_score", 0.0),
        "metrics": {},
        "misc": {},
    }


def save_result(result: dict[str, Any], output_path: str, artifact_ref: str) -> None:
    """Save result to JSON file."""
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    formatted = format_result(result, artifact_ref)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(formatted, f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description="Number Guessing Evaluator")
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument("--task-id", required=True, help="Task identifier")
    parser.add_argument("--subagent-id", default="", help="Subagent identifier")
    parser.add_argument("--artifact-ref", required=True, help="Guess text file path")
    parser.add_argument("--mode", default="full", help="Evaluation mode")
    parser.add_argument("--out", help="Output JSON file path")

    args = parser.parse_args()

    if not os.path.exists(args.artifact_ref):
        print(f"File not found: {args.artifact_ref}")
        sys.exit(1)

    result = evaluate(args.artifact_ref)
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

#!/usr/bin/env python
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""
Matrix Multiplication Tensor Decomposition Evaluator

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

BENCHMARK = 32  # Best known rank from Google
TARGET_VALUE = BENCHMARK


def validate_matrices(decomposition, n, m, p, rank):
    """Validate tensor decomposition matrices. Returns True if valid, False otherwise."""
    if np.isnan(decomposition[0]).any() or np.isnan(decomposition[1]).any() or np.isnan(decomposition[2]).any():
        return False
    if not all(isinstance(arr, np.ndarray) for arr in decomposition) or not decomposition:
        return False
    if any(arr.size == 0 for arr in decomposition):
        return False
    factor_matrix_1, factor_matrix_2, factor_matrix_3 = decomposition
    if factor_matrix_1.shape != (n * m, rank):
        return False
    if factor_matrix_2.shape != (m * p, rank):
        return False
    if factor_matrix_3.shape != (n * p, rank):
        return False
    return True


def verify_tensor_decomposition(decomposition, n, m, p, rank):
    if not all(isinstance(arr, np.ndarray) for arr in decomposition) or not decomposition:
        raise ValueError("Decomposition must be a tuple of NumPy arrays.")
    if any(arr.size == 0 for arr in decomposition):
        raise ValueError("One or more decomposition arrays are empty.")
    factor_matrix_1, factor_matrix_2, factor_matrix_3 = decomposition
    if factor_matrix_1.shape != (n * m, rank):
        raise ValueError(f"Expected shape {(n*m, rank)}, got {factor_matrix_1.shape}")
    if factor_matrix_2.shape != (m * p, rank):
        raise ValueError(f"Expected shape {(m*p, rank)}, got {factor_matrix_2.shape}")
    if factor_matrix_3.shape != (n * p, rank):
        raise ValueError(f"Expected shape {(n*p, rank)}, got {factor_matrix_3.shape}")
    matmul_tensor = np.zeros((n * m, m * p, n * p), dtype=np.float32)
    for i in range(n):
        for j in range(m):
            for k in range(p):
                matmul_tensor[i * m + j, j * p + k, k * n + i] = 1
    constructed_tensor = np.einsum("ir,jr,kr -> ijk", *decomposition)
    if not np.array_equal(constructed_tensor, matmul_tensor):
        diff = np.max(np.abs(constructed_tensor - matmul_tensor))
        raise ValueError(f"Tensor does not match. Max diff: {diff:.6e}")


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
    decomposition, n, m, p, loss, rank = program.run()
    import pickle
    with open('{temp_path_escaped}.results', 'wb') as f:
        pickle.dump({{'decomposition': decomposition, 'n': n, 'm': m, 'p': p, 'loss': loss, 'rank': rank}}, f)
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
                return results["decomposition"], results["n"], results["m"], results["p"], results["loss"], results["rank"]
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise TimeoutError(f"Execution timed out after {timeout_seconds} seconds")
    finally:
        for p in [temp_file_path, results_path]:
            if os.path.exists(p):
                os.unlink(p)


def evaluate(program_path: str, timeout_seconds: int = 600) -> dict[str, Any]:
    start_time = time.time()
    try:
        decomposition, n, m, p, loss, rank = run_program_with_timeout(program_path, timeout_seconds=timeout_seconds)
        eval_time = time.time() - start_time

        valid = validate_matrices(decomposition, n, m, p, rank)

        if valid:
            verify_tensor_decomposition(decomposition, n, m, p, rank)
            success_threshold = 1e-6
            if loss > success_threshold:
                print(f"Warning: Final loss {loss:.2e} is above success threshold.")
            inverse_rank = float(BENCHMARK / rank)
        else:
            inverse_rank = 0.0

        target_ratio = inverse_rank / TARGET_VALUE if valid else 0.0
        validity = 1.0 if valid else 0.0
        combined_score = target_ratio * validity

        return {
            "inverse_rank": float(inverse_rank),
            "target_ratio": float(target_ratio),
            "loss": float(loss),
            "rank": int(rank),
            "validity": float(validity),
            "eval_time": float(eval_time),
            "combined_score": float(combined_score),
        }
    except Exception as e:
        return {
            "inverse_rank": 0.0,
            "target_ratio": 0.0,
            "loss": 0.0,
            "rank": 0,
            "validity": 0.0,
            "eval_time": float(time.time() - start_time),
            "combined_score": 0.0,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }


def is_successful(r): return not r.get("traceback") and not r.get("error") and r.get("validity", 0) >= 1.0


def format_result(result: dict[str, Any], artifact_ref: str) -> dict[str, Any]:
    """Format result into status, fitness, metrics, misc."""
    success = is_successful(result)
    status = "success" if success else "failure"
    fitness = result.get("combined_score", 0.0)
    metric_keys = [
        "validity",
        "eval_time",
        "inverse_rank",
        "target_ratio",
        "loss",
        "rank",
    ]
    metrics = {}
    for key in metric_keys:
        if key in result:
            metrics[key] = result[key]
    profile = {key: result[key] for key in ["validity", "eval_time"] if key in result}
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

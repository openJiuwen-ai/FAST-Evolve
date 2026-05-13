#!/usr/bin/env python
"""
Uncertainty Inequality Evaluator

Usage:
    python evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref>

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
from typing import Any, Dict

import numpy as np
import sympy as sp

BENCHMARK = 0.3215872333529007
TARGET_VALUE = BENCHMARK
TIMEOUT_SECONDS = 600

x = sp.symbols("x")


def _hermite_4k_polys(m: int):
    degrees = [4 * k for k in range(m)]
    Hs = [sp.polys.orthopolys.hermite_poly(n=d, x=x, polys=False) for d in degrees]
    return Hs, degrees


def _construct_P_with_forced_zero(coeffs: np.ndarray) -> sp.Expr:
    m = len(coeffs)
    Hs, _ = _hermite_4k_polys(m + 1)
    rc = [sp.Rational(c) for c in coeffs]

    partial = sum(rc[i] * Hs[i] for i in range(m))
    a = Hs[m].subs(x, 0)
    b = -partial.subs(x, 0)
    c_last = sp.Rational(b) / sp.Rational(a)

    P = partial + c_last * Hs[m]

    if sp.limit(P, x, sp.oo) < 0:
        P = -P

    return sp.simplify(P)


def _largest_positive_root_of_P_over_x2(P: sp.Expr) -> float:
    gq = sp.exquo(P, x**2)
    roots = sp.real_roots(gq, x)
    if not roots:
        raise ValueError("No real roots for P(x)/x².")

    best = None
    for r in roots:
        r_approx = r.eval_rational(n=200)
        eps = sp.Rational(1, 10**198)
        left = gq.subs(x, r_approx - eps)
        right = gq.subs(x, r_approx + eps)
        if (left > 0 and right < 0) or (left < 0 and right > 0):
            if best is None or r_approx > best:
                best = r_approx

    if best is None:
        raise ValueError("No root with a verified sign change for P(x)/x².")
    return float(best)


def compute_c4_and_rmax(input_coeffs: np.ndarray):
    P = _construct_P_with_forced_zero(input_coeffs)
    assert P.subs(x, 0) == 0, "P(0) != 0 after forcing."
    assert sp.limit(P, x, sp.oo) > 0, "Limit at +inf is not positive."
    rmax = _largest_positive_root_of_P_over_x2(P)
    c4 = (rmax**2) / (2.0 * np.pi)
    return c4, rmax


def verify_c4_solution_strict(user_coeffs: np.ndarray, reported_c4: float, reported_rmax: float, atol=1e-9, rtol=1e-9):
    c4, rmax = compute_c4_and_rmax(np.asarray(user_coeffs, dtype=float))
    if not np.isclose(c4, reported_c4, rtol=rtol, atol=atol):
        raise ValueError(f"C4 mismatch: reported {reported_c4:.12f}, recomputed {c4:.12f}")
    if not np.isclose(rmax, reported_rmax, rtol=rtol, atol=atol):
        raise ValueError(f"r_max mismatch: reported {reported_rmax:.12f}, recomputed {rmax:.12f}")
    return c4, rmax


def validate_uncertainty(c4, c4_bound):
    """Validate uncertainty inequality solution. Returns True if valid, False otherwise."""
    c4_float = float(c4)
    c4_bound_float = float(c4_bound)
    if np.isnan(c4_float) or np.isnan(c4_bound_float):
        return False
    if np.isinf(c4_float) or np.isinf(c4_bound_float):
        return False
    if c4_bound_float <= 0:
        return False
    return True


def run_program(program_path: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location("program", program_path)
    program = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(program)
    return program.run()


def evaluate(program_path: str, timeout_seconds: int = TIMEOUT_SECONDS) -> Dict[str, Any]:
    start_time = time.time()

    try:
        coeffs, c4_bound, r_max = run_program(program_path)
        eval_time = time.time() - start_time

        coeffs = np.asarray(coeffs, dtype=float)
        c4, rmax = verify_c4_solution_strict(coeffs, float(c4_bound), float(r_max))

        valid = validate_uncertainty(c4, c4_bound)

        # For c4_bound: lower is better, so target_ratio = BENCHMARK / c4_bound when valid
        # This is a MINIMIZATION problem (lower c4_bound is better)
        target_ratio = float(BENCHMARK / c4) if valid and c4 > 0 else 0.0
        validity = 1.0 if valid else 0.0
        combined_score = target_ratio * validity

        return {
            "c4_bound": float(c4),
            "target_ratio": float(target_ratio),
            "combined_score": float(combined_score),
            "r_max": float(rmax),
            "coeffs": coeffs.tolist(),
            "validity": float(validity),
            "eval_time": float(eval_time),
        }

    except Exception as e:
        return {
            "c4_bound": 0.0,
            "target_ratio": 0.0,
            "combined_score": 0.0,
            "error": str(e),
            "traceback": traceback.format_exc(),
            "validity": 0.0,
            "eval_time": time.time() - start_time,
        }


def is_successful(result: Dict[str, Any]) -> bool:
    if result.get("traceback") or result.get("error"):
        return False
    return result.get("combined_score", 0) > 0


def format_result(result: Dict[str, Any], artifact_ref: str) -> Dict[str, Any]:
    success = is_successful(result)
    metrics = {}
    for key in ["validity", "eval_time"]:
        if key in result:
            metrics[key] = result[key]
    exclude_keys = {"combined_score", "validity", "eval_time", "c4_bound", "coeffs", "error", "traceback", "target_ratio", "r_max"}
    misc = {k: v for k, v in result.items() if k not in exclude_keys}
    return {
        "status": "success" if success else "failure",
        "artifact_ref": artifact_ref,
        "fitness": result.get("combined_score", 0.0),
        "metrics": metrics,
        "misc": misc,
    }


def save_result(result: Dict[str, Any], output_path: str, artifact_ref: str) -> None:
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    formatted = format_result(result, artifact_ref)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(formatted, f, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description="Uncertainty Inequality Evaluator")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--artifact-ref", required=True)
    parser.add_argument("--timeout", type=int, default=TIMEOUT_SECONDS)
    parser.add_argument("--out")
    args = parser.parse_args()

    if not os.path.exists(args.artifact_ref):
        print(f"Program file not found: {args.artifact_ref}")
        sys.exit(1)

    result = evaluate(args.artifact_ref, timeout_seconds=args.timeout)
    result["run_id"] = args.run_id
    result["task_id"] = args.task_id
    result["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")

    output_path = args.out or os.path.join(os.path.dirname(args.artifact_ref), "eval_result.json")
    save_result(result, output_path, args.artifact_ref)

    if is_successful(result):
        print(output_path)
        sys.exit(0)
    else:
        print(result.get("traceback", result.get("error", "Unknown error")))
        sys.exit(1)


if __name__ == "__main__":
    main()

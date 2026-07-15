#!/usr/bin/env python
"""
Signal Processing Evaluator

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
from scipy.stats import pearsonr
import concurrent.futures

TARGET_VALUE = 1.0


def run_with_timeout(func, args=(), kwargs={}, timeout_seconds=30):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func, *args, **kwargs)
        try:
            return future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(f"Function timed out after {timeout_seconds} seconds")


def safe_float(value):
    try:
        if np.isnan(value) or np.isinf(value):
            return 0.0
        return float(value)
    except:
        return 0.0


def calculate_slope_changes(signal_data):
    if len(signal_data) < 3:
        return 0
    diffs = np.diff(signal_data)
    sign_changes = 0
    for i in range(1, len(diffs)):
        if np.sign(diffs[i]) != np.sign(diffs[i - 1]) and diffs[i - 1] != 0:
            sign_changes += 1
    return sign_changes


def calculate_lag_error(filtered_signal, original_signal, window_size):
    if len(filtered_signal) == 0:
        return 1.0
    delay = window_size - 1
    if len(original_signal) <= delay:
        return 1.0
    recent_filtered = filtered_signal[-1]
    recent_original = original_signal[delay + len(filtered_signal) - 1]
    return abs(recent_filtered - recent_original)


def calculate_average_tracking_error(filtered_signal, original_signal, window_size):
    if len(filtered_signal) == 0:
        return 1.0
    delay = window_size - 1
    if len(original_signal) <= delay:
        return 1.0
    aligned_original = original_signal[delay: delay + len(filtered_signal)]
    min_length = min(len(filtered_signal), len(aligned_original))
    if min_length == 0:
        return 1.0
    filtered_aligned = filtered_signal[:min_length]
    original_aligned = aligned_original[:min_length]
    return np.mean(np.abs(filtered_aligned - original_aligned))


def validate_signal(result):
    """Validate signal processing result. Returns True if valid, False otherwise."""
    if not isinstance(result, dict):
        return False
    if "filtered_signal" not in result:
        return False
    filtered_signal = result["filtered_signal"]
    if len(filtered_signal) == 0:
        return False
    if np.isnan(np.array(filtered_signal)).any():
        return False
    return True


def calculate_false_reversal_penalty(filtered_signal, clean_signal, window_size):
    if len(filtered_signal) < 3 or len(clean_signal) < 3:
        return 0
    delay = window_size - 1
    if len(clean_signal) <= delay:
        return 1.0
    aligned_clean = clean_signal[delay: delay + len(filtered_signal)]
    min_length = min(len(filtered_signal), len(aligned_clean))
    if min_length < 3:
        return 0
    filtered_aligned = filtered_signal[:min_length]
    clean_aligned = aligned_clean[:min_length]
    filtered_diffs = np.diff(filtered_aligned)
    clean_diffs = np.diff(clean_aligned)
    false_reversals = 0
    for i in range(1, len(filtered_diffs)):
        filtered_change = np.sign(filtered_diffs[i]) != np.sign(filtered_diffs[i - 1]) and filtered_diffs[i - 1] != 0
        clean_change = np.sign(clean_diffs[i]) != np.sign(clean_diffs[i - 1]) and clean_diffs[i - 1] != 0
        if filtered_change and not clean_change:
            false_reversals += 1
    return false_reversals


def calculate_composite_score(S, L_recent, L_avg, R, alpha=[0.3, 0.2, 0.2, 0.3]):
    S_norm = min(S / 50.0, 2.0)
    L_recent_norm = min(L_recent, 2.0)
    L_avg_norm = min(L_avg, 2.0)
    R_norm = min(R / 25.0, 2.0)
    penalty = alpha[0] * S_norm + alpha[1] * L_recent_norm + alpha[2] * L_avg_norm + alpha[3] * R_norm
    score = 1.0 / (1.0 + penalty)
    return score


def generate_test_signals(num_signals=5):
    test_signals = []
    for i in range(num_signals):
        np.random.seed(42 + i)
        length = 500 + i * 100
        noise_level = 0.2 + i * 0.1
        t = np.linspace(0, 10, length)
        if i == 0:
            clean = 2 * np.sin(2 * np.pi * 0.5 * t) + 0.1 * t
        elif i == 1:
            clean = np.sin(2 * np.pi * 0.5 * t) + 0.5 * np.sin(2 * np.pi * 2 * t) + 0.2 * np.sin(2 * np.pi * 5 * t)
        elif i == 2:
            clean = np.sin(2 * np.pi * (0.5 + 0.2 * t) * t)
        elif i == 3:
            clean = np.concatenate([np.ones(length // 3), 2 * np.ones(length // 3), 0.5 * np.ones(length - 2 * (length // 3))])
        else:
            clean = np.cumsum(np.random.randn(length) * 0.1) + 0.05 * t
        noise = np.random.normal(0, noise_level, length)
        noisy = clean + noise
        test_signals.append((noisy, clean))
    return test_signals


def evaluate(program_path: str, timeout_seconds: int = 360) -> Dict[str, Any]:
    start_time = time.time()
    try:
        # Load the program
        import importlib.util
        spec = importlib.util.spec_from_file_location("program", program_path)
        program = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(program)

        if not hasattr(program, "run_signal_processing"):
            return {"combined_score": 0.0, "composite_score": 0.0, "overall_score": 0.0, "validity": 0.0, "error": "Missing run_signal_processing function"}

        test_signals = generate_test_signals(5)
        all_scores = []
        all_metrics = []
        successful_runs = 0

        for i, (noisy_signal, clean_signal) in enumerate(test_signals):
            try:
                exec_start_time = time.time()
                result = run_with_timeout(program.run_signal_processing, kwargs={"noisy_signal": noisy_signal, "window_size": 20}, timeout_seconds=10)
                execution_time = time.time() - exec_start_time

                if not validate_signal(result):
                    continue

                filtered_signal = result["filtered_signal"]
                filtered_signal = np.array(filtered_signal)
                window_size = 20

                S = calculate_slope_changes(filtered_signal)
                L_recent = calculate_lag_error(filtered_signal, noisy_signal, window_size)
                L_avg = calculate_average_tracking_error(filtered_signal, noisy_signal, window_size)
                R = calculate_false_reversal_penalty(filtered_signal, clean_signal, window_size)
                composite_score = calculate_composite_score(S, L_recent, L_avg, R)

                correlation = 0.0
                noise_reduction = 0.0
                try:
                    delay = window_size - 1
                    aligned_clean = clean_signal[delay: delay + len(filtered_signal)]
                    min_length = min(len(filtered_signal), len(aligned_clean))
                    if min_length > 1:
                        corr_result = pearsonr(filtered_signal[:min_length], aligned_clean[:min_length])
                        correlation = corr_result[0] if not np.isnan(corr_result[0]) else 0.0
                    aligned_noisy = noisy_signal[delay: delay + len(filtered_signal)]
                    aligned_noisy = aligned_noisy[:min_length]
                    aligned_clean = aligned_clean[:min_length]
                    if min_length > 0:
                        noise_before = np.var(aligned_noisy - aligned_clean)
                        noise_after = np.var(filtered_signal[:min_length] - aligned_clean)
                        noise_reduction = (noise_before - noise_after) / noise_before if noise_before > 0 else 0
                        noise_reduction = max(0, noise_reduction)
                except:
                    pass

                metrics = {"slope_changes": safe_float(S), "lag_error": safe_float(L_recent), "avg_error": safe_float(L_avg), "false_reversals": safe_float(R), "composite_score": safe_float(composite_score), "correlation": safe_float(correlation), "noise_reduction": safe_float(noise_reduction), "execution_time": safe_float(execution_time)}
                all_scores.append(composite_score)
                all_metrics.append(metrics)
                successful_runs += 1
            except:
                continue

        if successful_runs == 0:
            return {"combined_score": 0.0, "composite_score": 0.0, "overall_score": 0.0, "validity": 0.0, "error": "All test signals failed", "target_ratio": 0.0}

        avg_composite_score = np.mean(all_scores)
        avg_slope_changes = np.mean([m["slope_changes"] for m in all_metrics])
        avg_lag_error = np.mean([m["lag_error"] for m in all_metrics])
        avg_avg_error = np.mean([m["avg_error"] for m in all_metrics])
        avg_false_reversals = np.mean([m["false_reversals"] for m in all_metrics])
        avg_correlation = np.mean([m["correlation"] for m in all_metrics])
        avg_noise_reduction = np.mean([m["noise_reduction"] for m in all_metrics])
        avg_execution_time = np.mean([m["execution_time"] for m in all_metrics])
        success_rate = successful_runs / len(test_signals)

        smoothness_score = 1.0 / (1.0 + avg_slope_changes / 20.0)
        responsiveness_score = 1.0 / (1.0 + avg_lag_error)
        accuracy_score = max(0, avg_correlation)
        efficiency_score = min(1.0, 1.0 / max(0.001, avg_execution_time))

        overall_score = 0.4 * avg_composite_score + 0.2 * smoothness_score + 0.2 * accuracy_score + 0.1 * avg_noise_reduction + 0.1 * success_rate

        if accuracy_score < 0.1:
            overall_score = 0.0

        valid = successful_runs > 0
        target_ratio = float(overall_score / TARGET_VALUE) if valid else 0.0
        validity = 1.0 if valid else 0.0
        combined_score = target_ratio * validity

        eval_time = time.time() - start_time
        return {"combined_score": safe_float(combined_score), "composite_score": safe_float(avg_composite_score), "overall_score": safe_float(overall_score), "target_ratio": float(target_ratio), "slope_changes": safe_float(avg_slope_changes), "lag_error": safe_float(avg_lag_error), "avg_error": safe_float(avg_avg_error), "false_reversals": safe_float(avg_false_reversals), "correlation": safe_float(avg_correlation), "noise_reduction": safe_float(avg_noise_reduction), "smoothness_score": safe_float(smoothness_score), "accuracy_score": safe_float(accuracy_score), "success_rate": safe_float(success_rate), "eval_time": float(eval_time), "validity": float(validity)}

    except Exception as e:
        return {"combined_score": 0.0, "composite_score": 0.0, "overall_score": 0.0, "validity": 0.0, "error": str(e), "traceback": traceback.format_exc(), "target_ratio": 0.0}


def is_successful(r): return not r.get("traceback") and not r.get("error") and r.get("validity", 0) >= 1.0


def format_result(result: Dict[str, Any], artifact_ref: str) -> Dict[str, Any]:
    """Format result into status, fitness, metrics, misc."""
    success = is_successful(result)
    status = "success" if success else "failure"
    fitness = result.get("combined_score", 0.0)
    metric_keys = [
        "validity",
        "eval_time",
        "target_ratio",
        "composite_score",
        "overall_score",
        "slope_changes",
        "lag_error",
        "avg_error",
        "false_reversals",
        "correlation",
        "noise_reduction",
        "smoothness_score",
        "accuracy_score",
        "success_rate",
    ]
    metrics = {}
    for key in metric_keys:
        if key in result:
            metrics[key] = result[key]

    # Keep the historical lightweight diversity profile while exposing richer
    # evaluator feedback to Notes and meta-guidance through metrics.
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

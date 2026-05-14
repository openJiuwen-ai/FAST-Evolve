#!/usr/bin/env python
"""
KernelBench LayerNorm Evaluator - Standalone evaluation script

Evaluates a candidate LayerNorm kernel against the KernelBench reference
implementation, measuring speedup over PyTorch eager execution.

Usage:
    python evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref>

Output:
    - Success: prints result file path, exit code 0
    - Failure: prints error message, exit code 1
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict

# KernelBench problem specification
LEVEL = 1
PROBLEM_ID = 40

# Evaluation parameters
NUM_CORRECT_TRIALS = 5
NUM_PERF_TRIALS = 100
EVAL_TIMEOUT = 300


def ensure_run_and_check() -> Path:
    """Download run_and_check.py from KernelBench if not present."""
    evaluator_dir = Path(__file__).parent
    run_and_check_path = evaluator_dir / "run_and_check.py"

    if not run_and_check_path.exists():
        import urllib.request

        commit = "423217d"
        url = (
            f"https://raw.githubusercontent.com/ScalingIntelligence/"
            f"KernelBench/{commit}/scripts/run_and_check.py"
        )
        print(
            f"[INFO] Downloading run_and_check.py from KernelBench (commit {commit})...",
            file=sys.stderr,
        )
        try:
            urllib.request.urlretrieve(url, str(run_and_check_path))
            print(f"[INFO] Downloaded to {run_and_check_path}", file=sys.stderr)
        except Exception as e:
            raise RuntimeError(f"Failed to download run_and_check.py: {e}")

    return run_and_check_path


def convert_to_model_new(program_path: str) -> str:
    """Read program and ensure it uses class ModelNew. Returns temp file path."""
    with open(program_path, "r") as f:
        content = f.read()

    # Replace class Model with class ModelNew if not already
    if "class ModelNew" not in content:
        content = re.sub(
            r"^class Model(?=[(:])", "class ModelNew", content, flags=re.MULTILINE
        )
        content = re.sub(r"super\(Model,\s*self\)", "super()", content)
        content = re.sub(r"super\(Model,\s*cls\)", "super()", content)

    import tempfile

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, dir=Path(program_path).parent
    )
    tmp.write(content)
    tmp.close()
    return tmp.name


def check_kernelbench_available() -> bool:
    """Check if kernelbench package is installed."""
    try:
        import kernelbench  # noqa: F401

        return True
    except ImportError:
        return False


def evaluate(program_path: str, timeout_seconds: int = EVAL_TIMEOUT) -> Dict[str, Any]:
    """Evaluate a candidate kernel program against KernelBench reference.

    Returns dict with metrics including combined_score (speedup over eager).
    """
    start_time = time.time()

    if not os.path.exists(program_path):
        return {
            "combined_score": 0.0,
            "error": f"Program file not found: {program_path}",
        }

    if not check_kernelbench_available():
        return {
            "combined_score": 0.0,
            "error": "kernelbench package not installed. Install with: "
            "pip install 'kernelbench[gpu] @ git+https://github.com/ScalingIntelligence/KernelBench.git'",
        }

    # Convert program to ModelNew format
    kernel_src_path = convert_to_model_new(program_path)

    try:
        run_and_check_path = ensure_run_and_check()

        # Detect if program uses Triton
        with open(program_path, "r") as f:
            program_content = f.read()
        is_triton = bool(
            re.search(r"^(import triton|from triton)", program_content, flags=re.MULTILINE)
        )

        # Build command
        cmd = [
            sys.executable,
            str(run_and_check_path),
            "ref_origin=kernelbench",
            f"level={LEVEL}",
            f"problem_id={PROBLEM_ID}",
            f"kernel_src_path={kernel_src_path}",
            "eval_mode=local",
            "gpu=H100",
            f"num_correct_trials={NUM_CORRECT_TRIALS}",
            f"num_perf_trials={NUM_PERF_TRIALS}",
            f"timeout={timeout_seconds}",
            "check_kernel=False",
        ]

        if is_triton:
            cmd.append("backend=triton")

        env = os.environ.copy()

        print(f"[INFO] Running: {' '.join(cmd)}", file=sys.stderr)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=str(Path(__file__).parent),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "combined_score": 0.0,
            "error": f"Evaluation timed out after {timeout_seconds} seconds",
            "eval_time": time.time() - start_time,
        }
    except Exception as e:
        return {
            "combined_score": 0.0,
            "error": f"Evaluation setup failed: {str(e)}",
            "traceback": traceback.format_exc(),
            "eval_time": time.time() - start_time,
        }
    finally:
        # Clean up temp file
        try:
            if kernel_src_path != program_path:
                os.unlink(kernel_src_path)
        except Exception:
            pass

    eval_time = time.time() - start_time
    stdout = result.stdout
    stderr = result.stderr

    if result.returncode != 0:
        error_msg = stderr[:500] if stderr else f"Exit code {result.returncode}"
        return {
            "combined_score": 0.0,
            "error": f"Subprocess failed: {error_msg}",
            "eval_time": eval_time,
        }

    # Parse speedup from output
    speedup_eager = None
    speedup_compile = None
    kernel_time = None
    ref_eager_time = None

    for line in stdout.split("\n"):
        if "Speedup over eager:" in line:
            match = re.search(r"([0-9.]+)x", line)
            if match:
                speedup_eager = float(match.group(1))
        elif "Speedup over torch.compile:" in line:
            match = re.search(r"([0-9.]+)x", line)
            if match:
                speedup_compile = float(match.group(1))
        elif "Custom Kernel exec time:" in line:
            match = re.search(r"([0-9.]+) ms", line)
            if match:
                kernel_time = float(match.group(1))
        elif "PyTorch Reference Eager exec time:" in line:
            match = re.search(r"([0-9.]+) ms", line)
            if match:
                ref_eager_time = float(match.group(1))

    if speedup_eager is not None and speedup_eager > 0:
        return {
            "combined_score": float(speedup_eager),
            "speedup_over_eager": float(speedup_eager),
            "speedup_over_compile": float(speedup_compile) if speedup_compile else None,
            "kernel_time_ms": kernel_time,
            "ref_eager_time_ms": ref_eager_time,
            "eval_time": eval_time,
        }

    # Kernel failed correctness or didn't compile
    stdout_excerpt = stdout
    if "[Eval]" in stdout:
        eval_start = stdout.find("[Eval]")
        stdout_excerpt = stdout[eval_start:]
    if len(stdout_excerpt) > 5000:
        stdout_excerpt = stdout_excerpt[-5000:]

    return {
        "combined_score": 0.0,
        "error": "Kernel failed correctness check or did not compile",
        "stdout_excerpt": stdout_excerpt,
        "eval_time": eval_time,
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
    if result.get("combined_score", 0.0) <= 0.0:
        return False
    return True


def get_error_message(result: Dict[str, Any]) -> str:
    """Extract error message from result."""
    if result.get("traceback"):
        return result["traceback"]
    if result.get("error"):
        return str(result["error"])
    if result.get("combined_score", 0.0) <= 0.0:
        return "Kernel failed: no speedup measured"
    return "Unknown error"


def format_result(result: Dict[str, Any], artifact_ref: str) -> Dict[str, Any]:
    """Format result into status, fitness, metrics, misc."""
    success = is_successful(result)

    status = "success" if success else "failure"
    fitness = result.get("combined_score", 0.0)

    metrics = {}
    for key in ["speedup_over_eager", "speedup_over_compile", "kernel_time_ms", "ref_eager_time_ms"]:
        if key in result and result[key] is not None:
            metrics[key] = result[key]

    exclude_keys = {
        "combined_score",
        "speedup_over_eager",
        "speedup_over_compile",
        "kernel_time_ms",
        "ref_eager_time_ms",
        "run_id",
        "task_id",
        "subagent_id",
        "timestamp",
    }
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
    parser = argparse.ArgumentParser(description="KernelBench LayerNorm Evaluator")
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument("--task-id", required=True, help="Task identifier")
    parser.add_argument("--subagent-id", default="", help="Subagent identifier")
    parser.add_argument("--artifact-ref", required=True, help="Program artifact path")
    parser.add_argument("--mode", default="full", help="Evaluation mode")
    parser.add_argument("--timeout", type=int, default=EVAL_TIMEOUT, help="Timeout in seconds")
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
    output_path = args.out or os.path.join(
        os.path.dirname(program_path), "eval_result.json"
    )

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

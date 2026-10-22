"""
KernelBench evaluator with structured JSON output.

Usage:
    uv run python evaluator_dual.py \
        --run-id <run_id> --task-id <task_id> \
        --artifact-ref <solution_path> \
        --out <output_json_path>
"""

import os
import re
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path


def ensure_run_and_check(evaluator_dir: Path):
    """Download run_and_check.py if not present."""
    run_and_check_path = evaluator_dir / "run_and_check.py"

    if not run_and_check_path.exists():
        import urllib.request

        commit = "423217d"
        url = f"https://raw.githubusercontent.com/ScalingIntelligence/KernelBench/{commit}/scripts/run_and_check.py"

        print(
            f"[INFO] Downloading run_and_check.py from KernelBench (commit {commit})...",
            file=sys.stderr,
        )
        try:
            urllib.request.urlretrieve(url, run_and_check_path)
            print(f"[INFO] Downloaded to {run_and_check_path}", file=sys.stderr)
        except Exception as e:
            raise RuntimeError(f"Failed to download run_and_check.py: {e}")

    return run_and_check_path


def evaluate(program_path: str):
    """Evaluate a candidate kernel program against the reference using run_and_check.py."""
    try:
        level = int(os.environ.get("KERNELBENCH_LEVEL", "1"))
        problem_id = int(os.environ.get("KERNELBENCH_PROBLEM_ID", "40"))
        eval_mode = os.environ.get("KERNELBENCH_EVAL_MODE", "local")
        gpu = os.environ.get("KERNELBENCH_GPU", "H100")
        num_correct_trials = int(os.environ.get("KERNELBENCH_NUM_CORRECT_TRIALS", "5"))
        num_perf_trials = int(os.environ.get("KERNELBENCH_NUM_PERF_TRIALS", "100"))
        timeout = int(os.environ.get("KERNELBENCH_TIMEOUT", "300"))

        with open(program_path) as f:
            program_content = f.read()

        is_triton = bool(
            re.search(r"^(import triton|from triton)", program_content, flags=re.MULTILINE)
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tmp_file:
            converted_content = program_content
            if "class ModelNew" not in converted_content:
                converted_content = re.sub(
                    r"^class Model(?=[(:])", "class ModelNew", converted_content, flags=re.MULTILINE
                )
                converted_content = re.sub(r"super\(Model,\s*self\)", "super()", converted_content)
                converted_content = re.sub(r"super\(Model,\s*cls\)", "super()", converted_content)

            tmp_file.write(converted_content)
            kernel_src_path = tmp_file.name

        try:
            evaluator_dir = Path(__file__).parent
            run_and_check_path = ensure_run_and_check(evaluator_dir)

            cmd = [
                sys.executable,
                str(run_and_check_path),
                "ref_origin=kernelbench",
                f"level={level}",
                f"problem_id={problem_id}",
                f"kernel_src_path={kernel_src_path}",
                f"eval_mode={eval_mode}",
                f"gpu={gpu}",
                f"num_correct_trials={num_correct_trials}",
                f"num_perf_trials={num_perf_trials}",
                f"timeout={timeout}",
                "check_kernel=False",
            ]

            if is_triton:
                cmd.append("backend=triton")

            env = os.environ.copy()

            print(f"[INFO] Running evaluation command: {' '.join(cmd)}", file=sys.stderr)
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(evaluator_dir),
                env=env,
            )
        finally:
            try:
                os.unlink(kernel_src_path)
            except Exception:
                pass

        stdout = result.stdout
        stderr = result.stderr

        if result.returncode != 0:
            print(
                f"[ERROR] Evaluation failed with return code {result.returncode}", file=sys.stderr
            )
            print(f"[ERROR] stdout: {stdout}", file=sys.stderr)
            print(f"[ERROR] stderr: {stderr}", file=sys.stderr)
            return {
                "combined_score": -100.0,
                "error": f"Evaluation subprocess failed: {stderr[:500]}",
                "return_code": result.returncode,
            }

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
                "speedup_over_eager": speedup_eager,
                "speedup_over_compile": speedup_compile,
                "kernel_time_ms": kernel_time,
                "ref_eager_time_ms": ref_eager_time,
                "eval_mode": eval_mode,
                "gpu": gpu,
            }
        else:
            stdout_excerpt = stdout
            if "[Eval]" in stdout:
                eval_start = stdout.find("[Eval]")
                stdout_excerpt = stdout[eval_start:]

            if len(stdout_excerpt) > 5000:
                stdout_excerpt = stdout_excerpt[-5000:]

            return {
                "combined_score": -100.0,
                "error": "Kernel failed correctness check or did not compile",
                "stdout_excerpt": stdout_excerpt,
            }

    except subprocess.TimeoutExpired:
        return {
            "combined_score": -1.0,
            "error": f"Evaluation timed out after {timeout} seconds",
        }
    except Exception as e:
        traceback.print_exc()
        return {
            "combined_score": -100.0,
            "error": f"Error during evaluation: {str(e)}",
            "error_type": type(e).__name__,
        }


if __name__ == "__main__":
    from wrapper import run
    os.environ["CUDA_VISIBLE_DEVICES"] = "6"
    run(evaluate)

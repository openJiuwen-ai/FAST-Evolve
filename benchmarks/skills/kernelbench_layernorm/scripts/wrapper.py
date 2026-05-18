"""Backwards-compat wrapper for old Python-based evaluators with structured JSON output.

Usage — add this to the bottom of your evaluator.py::

    if __name__ == "__main__":
        from wrapper_dual import run
        run(evaluate)

Accepts CLI arguments:
    --run-id        Run ID
    --task-id       Task ID
    --artifact-ref  Path to the solution file
    --out           Output file path for eval result JSON
"""

import argparse
import json
import sys
import time
import traceback
from pathlib import Path


def run(evaluate_fn):
    if len(sys.argv) < 2:
        print("Usage: evaluator.py --run-id <id> --task-id <id> --artifact-ref <path> --out <path>", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Evaluator wrapper")
    parser.add_argument("--run-id", required=True, help="Run ID")
    parser.add_argument("--task-id", required=True, help="Task ID")
    parser.add_argument("--artifact-ref", required=True, help="Path to the solution file")
    parser.add_argument("--out", required=True, help="Output file path for eval result JSON")
    args = parser.parse_args()

    program_path = args.artifact_ref

    # Redirect stdout → stderr during evaluation so debug prints
    # don't contaminate the JSON output.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr

    start_time = time.time()
    try:
        result = evaluate_fn(program_path)
    except Exception as e:
        sys.stdout = real_stdout
        eval_time = time.time() - start_time
        output = {
            "status": "error",
            "artifact_ref": args.artifact_ref,
            "fitness": 0.0,
            "metrics": {
                "combined_score": 0.0,
                "validity": 0.0,
                "eval_time": eval_time,
            },
            "misc": {
                "error": str(e),
                "traceback": traceback.format_exc(),
                "run_id": args.run_id,
                "task_id": args.task_id,
                "subagent_id": "",
                "mode": "full",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
        }
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(output, indent=2))
        print(f"[INFO] Eval result written to {out_path}")
        return

    eval_time = time.time() - start_time
    sys.stdout = real_stdout

    if not isinstance(result, dict):
        output = {
            "status": "error",
            "artifact_ref": args.artifact_ref,
            "fitness": 0.0,
            "metrics": {
                "combined_score": 0.0,
                "validity": 0.0,
                "eval_time": eval_time,
            },
            "misc": {
                "error": f"evaluate() returned {type(result).__name__}, expected dict",
                "run_id": args.run_id,
                "task_id": args.task_id,
                "subagent_id": "",
                "mode": "full",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
        }
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(output, indent=2))
        print(f"[INFO] Eval result written to {out_path}")
        return

    # Separate numeric metrics from non-numeric misc.
    metrics = {}
    misc = {}
    for k, v in result.items():
        if isinstance(v, bool):
            metrics[k] = float(v)
        elif isinstance(v, (int, float)):
            metrics[k] = float(v)
        elif isinstance(v, str):
            misc[k] = v
        elif isinstance(v, (list, dict)):
            misc[k] = json.dumps(v)

    if "combined_score" not in metrics:
        metrics["combined_score"] = 0.0

    status = "error" if "error" in misc else "success"
    fitness = float(result.get("speedup_over_eager", result.get("combined_score", 0.0)))

    metrics["validity"] = 1.0 if status == "success" else 0.0
    metrics["eval_time"] = eval_time

    misc["run_id"] = args.run_id
    misc["task_id"] = args.task_id
    misc["subagent_id"] = ""
    misc["mode"] = "full"
    misc["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")

    output = {
        "status": status,
        "artifact_ref": args.artifact_ref,
        "fitness": fitness,
        "metrics": metrics,
        "misc": misc,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))
    print(f"[INFO] Eval result written to {out_path}")

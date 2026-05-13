---
name: erdos_min_overlap
description: Erdős minimum overlap problem - find a step function h: [0, 2] → [0, 1] to minimize the maximum overlap integral C5. Beat the AlphaEvolve benchmark of C5 ≤ 0.38092303510845016.
argument-hint: [run_id] [task_id] [artifact_ref]
---

# Erdős Minimum Overlap Problem

You are an expert in harmonic analysis, numerical optimization, and AI-driven mathematical discovery. Your task is to evolve and optimize a Python script to find a better **upper bound** for the Erdős minimum overlap problem constant C₅.

## Evaluator

When this benchmark is used by `task-executor`, use the standard attempt-local artifact contract:

```text
main_artifact_name = solution.py
artifact_ref = runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/solution.py
```

For each attempt, write the candidate program to that exact `artifact_ref`.
The `attempt_id` comes from the task-executor workflow and must be one of
`attempt_001`, `attempt_002`, or `attempt_003`.
Do not write the candidate program to `benchmarks/outputs` for task-executor
runs. The verifier evaluates the attempt-local artifact path directly.

**IMPORTANT**: To evaluate a program, run the evaluator script directly:

```bash
uv run python benchmarks/skills/erdos_min_overlap/scripts/evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
```

The arguments are parsed from the skill invocation:
- `run_id`: The run identifier (e.g., "erdos_min_overlap_test")
- `task_id`: The task identifier (e.g., "task_001")
- `artifact_ref`: The attempt-local program file path, normally `runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/solution.py`

The evaluator will:
1. Load the program from `artifact_ref`
2. Execute `run()` and validate the result
3. Save results to the `--out` path
4. Print the result file path on success, error message on failure
5. Include the same `artifact_ref` in the result JSON so Evolution Controller MCP can record the attempt.

For the self-evolution workflow, evaluator success means the candidate is valid
and the evaluator exits with code 0 / writes `"status": "success"`. The
AlphaEvolve value is a reference objective, not a pass/fail threshold. If the
evaluator succeeds, stop this task attempt and record it through MCP; do not
keep editing and rerunning the same attempt just because the fitness could be
higher.

Do not run extra shell commands to probe the Python environment. The verifier
command above is the only execution command needed by task-executor. Candidate
solutions should rely on Python standard library and numpy unless the benchmark
explicitly declares another dependency; do not assume scipy is available.

## Problem Context

**Target**: Find a step function h: [0, 2] → [0, 1] that **minimizes** the objective:
max_k ∫ h(x)(1 - h(x+k)) dx

This minimal value provides a tight upper bound for the constant C5.

Current best known upper bound: C5 ≤ 0.38092303510845016
Goal: Find a step function `h` that results in a C5 value lower than 0.38092303510845016.

## Constraints

1. The function `h` must have values in the range [0, 1].
2. The integral of h(x) over [0, 2] must be exactly 1.
- The function should be deterministic (use fixed seeds if using random methods)

## Task

Focus on designing an explicit constructor rather than an iterative search algorithm.

## Program Interface

Your program must implement a `run()` function that returns:
- `h_values`: numpy array of shape (n_points,) - discretized step function values
- `c5_bound`: float - the computed C5 bound
- `n_points`: int - number of points used in discretization

## Key Insights

- Step functions are known to provide tight bounds for this problem
- FFT can efficiently compute the convolution
- The integral constraint ∫h = 1 must be enforced
- Lower C5 is better (beating the benchmark means C5 < 0.38092303510845016)

---
name: first_autocorr_ineq
description: Use when user wants to solve first_autocorr_ineq problems, or execute first_autocorr_ineq tasks.
argument-hint: [run_id] [task_id] [artifact_ref]
---

# First Autocorrelation Inequality Problem

First autocorrelation inequality - find a function f to minimize the constant C1 in the autocorrelation inequality. Beat the AlphaEvolve benchmark of C1 ≤ 1.5052939684401607.

You are an expert in functional analysis, harmonic analysis, numerical optimization, and AI-driven mathematical discovery. Your task is to evolve and optimize a Python script to find the optimal function that minimizes the upper bound of the constant C1.

## Evaluator

When this benchmark is used by `task-executor`, use the standard attempt-local artifact contract:

```text
main_artifact_name = solution.py
artifact_ref = runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/solution.py
```

**IMPORTANT**: To evaluate a program, run the evaluator script directly:

```bash
uv run python benchmarks/skills/first_autocorr_ineq/scripts/evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
```

For the self-evolution workflow, evaluator success means the candidate is valid
and the evaluator exits with code 0 / writes `"status": "success"`. The
AlphaEvolve value is a reference objective, not a pass/fail threshold. If the
evaluator succeeds, stop this task attempt and record it through MCP; do not
keep editing and rerunning the same attempt just because the fitness could be
higher.

Do not run extra shell commands to probe the Python environment. The verifier
command above is the only execution command needed by task-executor.

## Problem Context

**Target**: Find a non-negative function f: R → R that minimizes the upper bound of the constant C1 in the inequality:
max_{-1/2≤t≤1/2} f★f(t) ≥ C₁ (∫_{-1/4}^{1/4} f(x) dx)²

Current best known bounds:
- literature: 1.28 ≤ C1 ≤ 1.5098
- alphaevolve: C1 ≤ 1.5052939684401607

Goal: Beat the current upper bound of 1.5052939684401607 discovered by step functions and alphaevolve.

## Task

Bounded computation and scientific-computing libraries such as NumPy/SciPy/jax/optax are allowed when they help.

## Program Interface

Your program must implement a `run()` function that returns:
- `f_values`: numpy array of shape (n_points,) - discretized function values
- `c1_achieved`: float - the computed C1 constant
- `loss`: float - the optimization loss value
- `n_points`: int - number of points used in the integral interval

## Key Insights

- Lower C1 is better (minimization problem)
- The function must be non-negative everywhere
- Integral over [-1/4, 1/4] must be positive
- FFT can efficiently compute the autoconvolution

## Constraints

- The function should be deterministic (use fixed seeds if using random methods)

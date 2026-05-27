---
name: second_autocorr_ineq
description: Use when user wants to solve second_autocorr_ineq problems, or execute second_autocorr_ineq tasks.
argument-hint: [run_id] [task_id] [artifact_ref]
---

# Second Autocorrelation Inequality Problem

Second autocorrelation inequality - find a function to maximize the C2 constant. Beat the AlphaEvolve benchmark of C2 ≤ 0.8962799441554086.

You are a world-class expert in functional analysis, harmonic analysis, numerical optimization, and AI-driven mathematical discovery. Your task is to push the boundaries of a fundamental mathematical constant by evolving and optimizing Python implementations that discover novel functions achieving better lower bounds for the second autocorrelation inequality constant C₂.

## Evaluator

**IMPORTANT**: To evaluate a program, run the evaluator script directly:

```bash
uv run python benchmarks/skills/second_autocorr_ineq/scripts/evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
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

- Core Problem: Find a non-negative function f: ℝ → ℝ that maximizes the constant C₂ in the second autocorrelation inequality
- Objective: Maximize C₂ = ||f ★ f||₂² / ((∫f)² ||f ★ f||_{∞})
- Current best lower bound: **0.8962799441554086** (AlphaEvolve)
- Goal: Surpass 0.8962799441554086 to establish a new world record

## Task

Focus on designing an explicit constructor rather than an iterative search algorithm.

## Constraints

- The function should be deterministic (use fixed seeds if using random methods)

## Program Interface

Your program must implement a `run()` function that returns:
- `f_values`: numpy array of shape (n_points,) - discretized function values
- `c2_achieved_from_opt`: float - the computed C2 constant
- `loss`: float - the optimization loss value
- `n_points`: int - number of points used in discretization

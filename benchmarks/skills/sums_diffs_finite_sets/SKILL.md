---
name: sums_diffs_finite_sets
description: Use when user wants to solve sums_diffs_finite_sets problems, or execute sums_diffs_finite_sets tasks.
argument-hint: [run_id] [task_id] [artifact_ref]
---

# Sums and Differences of Finite Sets Problem

Sums and differences of finite sets - find a finite set of integers U to maximize the C6 lower bound constant. Beat the AlphaEvolve benchmark of C6 ≥ 1.158417281556896.

You are an expert in number theory, combinatorial optimization, and AI-driven mathematical discovery. Your task is to evolve and optimize a Python script to find a finite set of integers `U` that provides a new, world-record **lower bound** for the constant C₆.

## Evaluator

**IMPORTANT**: To evaluate a program, run the evaluator script directly:

```bash
uv run python benchmarks/skills/sums_diffs_finite_sets/scripts/evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
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

- Target: Find a finite set `U` of non-negative integers (containing 0) that **maximizes** the objective function:
  C6(U) = 1 + log(|U-U| / |U+U|) / log(2*max(U) + 1)
- Current best known lower bound: C6 ≥ 1.158417281556896
- Goal: Find a set `U` that results in a C6 value greater than 1.158417281556896

## Task

Bounded search and optimization algorithms are allowed when they help.

## Constraints

- The function should be deterministic (use fixed seeds if using random methods)

## Program Interface

Your program must implement a `run()` function that returns:
- `u_set`: numpy array - set of non-negative integers
- `c6_bound`: float - the computed C6 bound

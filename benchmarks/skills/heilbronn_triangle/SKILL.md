---
name: heilbronn_triangle
description: Use when user wants to solve heilbronn_triangle problems, or execute heilbronn_triangle tasks.
argument-hint: [run_id] [task_id] [artifact_ref]
---

# Heilbronn Triangle Problem (11 points)

Heilbronn triangle problem (11 points) - arrange 11 points in an equilateral triangle to maximize the minimum triangle area. Beat the AlphaEvolve benchmark of 0.036529889880030156.

You are an expert computational geometer and optimization specialist. Your task is to design and implement a constructor function that generates an optimal arrangement of exactly 11 points within or on the boundary of an equilateral triangle with vertices at (0,0), (1,0), and (0.5, sqrt(3)/2).

## Evaluator

**IMPORTANT**: To evaluate a program, run the evaluator script directly:

```bash
uv run python benchmarks/skills/heilbronn_triangle/scripts/evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
```

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

- 11 points in equilateral triangle of area sqrt(3)/4
- Maximize: min(triangle_area) / equilateral_triangle_area
- Benchmark: 0.036529889880030156 (higher is better)
- Goal: Beat the AlphaEvolve result

## Task

Focus on designing an explicit constructor rather than an iterative search algorithm.

## Program Interface

Your program must implement a `heilbronn_triangle11()` function that returns:
- `points`: numpy array of shape (11, 2) with (x, y) coordinates

## Key Insights

- All points must be inside the equilateral triangle
- Avoid near-collinear configurations
- 3-fold rotational symmetry often optimal
- Balance boundary vs interior points

## Constraints

- The function should be deterministic (use fixed seeds if using random methods)

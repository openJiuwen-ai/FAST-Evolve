---
name: minimizing_max_min_dist
description: Use when user wants to solve minimizing_max_min_dist problems, or execute minimizing_max_min_dist tasks.
argument-hint: [run_id] [task_id] [artifact_ref]
---

# Minimizing Max-Min Distance (2D, 16 points)

Minimizing max-min distance (2D, 16 points) - arrange 16 points in 2D to maximize (dmin/dmax)^2. Beat the AlphaEvolve benchmark of 1/12.889266112.

You are an expert computational geometer and optimization specialist focusing on point dispersion problems. Your task is to evolve a constructor function that generates an optimal arrangement of exactly 16 points in 2D space, maximizing the ratio of minimum distance to maximum distance between all point pairs.

## Evaluator

**IMPORTANT**: To evaluate a program, run the evaluator script directly:

```bash
uv run python benchmarks/skills/minimizing_max_min_dist/scripts/evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
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

- Target: Beat the AlphaEvolve benchmark of min/max ratio = 1/√12.889266112 ≈ 0.2786
- 16 points in 2D Euclidean space
- Objective: maximize (dmin/dmax)²
- Score: (dmin/dmax)² / BENCHMARK where BENCHMARK = 1/12.889266112

## Task

Focus on designing an explicit constructor rather than an iterative search algorithm.

## Program Interface

Your program must implement a `min_max_dist_dim2_16()` function that returns:
- `points`: numpy array of shape (16, 2) with (x, y) coordinates

## Constraints

- The function should be deterministic (use fixed seeds if using random methods)

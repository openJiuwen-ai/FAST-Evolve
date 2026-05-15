---
name: heilbronn_convex
description: Use when user wants to solve heilbronn_convex problems, or execute heilbronn_convex tasks.
argument-hint: [run_id] [task_id] [artifact_ref]
---

# Heilbronn Problem on Convex Region (13 points)

Heilbronn convex region problem (13 points) - arrange 13 points to maximize the minimum triangle area normalized by convex hull area. Beat the AlphaEvolve benchmark of 0.030936889034895654.

You are an expert computational geometer and optimization specialist. Your task is to design and implement a constructor function that generates an optimal arrangement of exactly 13 points within or on the boundary of a unit-area convex region to maximize the minimum triangle area formed by any three points.

## Evaluator

**IMPORTANT**: To evaluate a program, run the evaluator script directly:

```bash
uv run python benchmarks/skills/heilbronn_convex/scripts/evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
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

- 13 points in a convex region
- Maximize: min(triangle_area) / convex_hull_area
- Benchmark: 0.030936889034895654 (higher is better)
- Goal: Beat the AlphaEvolve result

## Task

Focus on designing an explicit constructor rather than an iterative search algorithm.

## Program Interface

Your program must implement a `heilbronn_convex13()` function that returns:
- `points`: numpy array of shape (13, 2) with (x, y) coordinates

## Key Insights

- Use convex hull area for normalization
- Avoid near-collinear configurations
- 3-fold rotational symmetry often appears in optimal configurations
- Balance between boundary and interior points

## Constraints

- The function should be deterministic (use fixed seeds if using random methods)

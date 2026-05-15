---
name: hexagon_packing
description: Use when user wants to solve hexagon_packing problems, or execute hexagon_packing tasks.
argument-hint: [run_id] [task_id] [artifact_ref]
---

# Hexagon Packing Problem (11 unit hexagons)

Hexagon packing problem (11 unit hexagons) - pack 11 unit regular hexagons inside a larger regular hexagon to maximize 1/outer_hex_side_length. Beat the AlphaEvolve benchmark of 1/3.930092 ≈ 0.2544.

You are an expert computational geometer and optimization specialist focusing on hexagon packing problems. Your task is to evolve a constructor function that generates an optimal arrangement of exactly 11 unit regular hexagons within a larger regular hexagon, maximizing 1/outer_hex_side_length.

## Evaluator

**IMPORTANT**: To evaluate a program, run the evaluator script directly:

```bash
uv run python benchmarks/skills/hexagon_packing/scripts/evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
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

- Target: Beat the current state-of-the-art benchmark of 1/outer_hex_side_length = 1/3.930092 ≈ 0.2544
- Constraint: All 11 inner hexagons must be unit regular hexagons (side length = 1) that are fully contained within the outer hexagon with no overlaps
- Objective: maximize 1/R where R is the outer hexagon side length

## Task

Focus on designing an explicit constructor rather than an iterative search algorithm.

## Program Interface

Your program must implement a `hexagon_packing_11()` function that returns:
- `inner_hex_data`: numpy array of shape (11, 3) - each row is (x, y, angle_degrees)
- `outer_hex_data`: numpy array of shape (3,) - (x, y, angle_degree)
- `outer_hex_side_length`: float - side length of outer hexagon

## Constraints

- The function should be deterministic (use fixed seeds if using random methods)

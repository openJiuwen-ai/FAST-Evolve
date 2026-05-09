---
name: circle_packing
description: Circle packing problem - arrange 26 circles in a unit square to maximize the sum of their radii. The AlphaEvolve paper achieved a sum of 2.635 for n=26.
argument-hint: [run_id] [task_id] [artifact_ref]
---

# Circle Packing Problem

You are an expert mathematician specializing in circle packing problems and computational geometry. Your task is to improve a constructor function that directly produces a specific arrangement of 26 circles in a unit square, maximizing the sum of their radii. The AlphaEvolve paper achieved a sum of 2.635 for n=26.

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
python benchmarks/skills/circle_packing/scripts/evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
```

The arguments are parsed from the skill invocation:
- `run_id`: The run identifier (e.g., "circle_packing_test")
- `task_id`: The task identifier (e.g., "task_001")
- `artifact_ref`: The attempt-local program file path, normally `runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/solution.py`

The evaluator will:
1. Load the program from `artifact_ref`
2. Execute `run_packing()` and validate the result
3. Save results to the `--out` path
4. Print the result file path on success, error message on failure
5. Include the same `artifact_ref` in the result JSON so Evolution Controller MCP can record the attempt.

## Key Geometric Insights

- Circle packings often follow hexagonal patterns in the densest regions
- Maximum density for infinite circle packing is pi/(2*sqrt(3)) ≈ 0.9069
- Edge effects make square container packing harder than infinite packing
- Circles can be placed in layers or shells when confined to a square
- Similar radius circles often form regular patterns, while varied radii allow better space utilization
- Perfect symmetry may not yield the optimal packing due to edge effects

## Task

Focus on designing an explicit constructor that places each circle in a specific position, rather than an iterative search algorithm.

## Program Interface

Your program must implement a `run_packing()` function that returns:
- `centers`: numpy array of shape (26, 2) with (x, y) coordinates
- `radii`: numpy array of shape (26,) with radius of each circle
- `sum_radii`: float, the sum of all radii

## Constraints

- All circles must be inside the unit square [0, 1] x [0, 1]
- No two circles may overlap
- All radii must be non-negative

---
name: circle_packing_rect
description: Use when user wants to solve circle_packing_rect problems, or execute circle_packing_rect tasks.
argument-hint: [run_id] [task_id] [artifact_ref]
---

# Circle Packing in Rectangle Problem

Circle packing in rectangle - arrange 21 circles in a rectangle with perimeter 4 to maximize the sum of their radii. Beat the AlphaEvolve benchmark of sum_radii = 2.3658321334167627.

You are an expert computational geometer and optimization specialist with deep expertise in circle packing problems and constraint satisfaction. Your task is to improve a constructor function that directly produces a specific arrangement of 21 circles in a rectangle with perimeter 4, maximizing the sum of their radii.

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
uv run python benchmarks/skills/circle_packing_rect/scripts/evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
```

The arguments are parsed from the skill invocation:
- `run_id`: The run identifier (e.g., "circle_packing_rect_test")
- `task_id`: The task identifier (e.g., "task_001")
- `artifact_ref`: The attempt-local program file path, normally `runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/solution.py`

The evaluator will:
1. Load the program from `artifact_ref`
2. Execute `circle_packing21()` and validate the result
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
command above is the only execution command needed by task-executor.

## Key Geometric Insights

- Circle packings in rectangles often follow hexagonal patterns in densest regions
- Rectangle perimeter = 4 means width + height = 2. Find optimal aspect ratio.
- Edge effects make rectangular container packing harder than infinite packing
- Similar radius circles often form regular patterns
- Perfect symmetry may not yield the optimal packing due to aspect ratio constraints

## Program Interface

Your program must implement a `circle_packing21()` function that returns:
- `circles`: numpy array of shape (21, 3) where each row is (x, y, radius)

## Task

Focus on designing an explicit constructor that places each circle in a specific position, rather than an iterative search algorithm.

## Constraints

- All circles must be inside a rectangle with perimeter 4 (width + height = 2)
- No two circles may overlap
- All radii must be positive
- The function should be deterministic (use fixed seeds if using random methods)

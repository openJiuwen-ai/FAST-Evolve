---
name: circle_packing
description: Circle packing problem - arrange 26 circles in a unit square to maximize the sum of their radii. The AlphaEvolve paper achieved a sum of 2.635 for n=26.
argument-hint: [run_id] [task_id] [program_name]
---

# Circle Packing Problem

You are an expert mathematician specializing in circle packing problems and computational geometry. Your task is to improve a constructor function that directly produces a specific arrangement of 26 circles in a unit square, maximizing the sum of their radii. The AlphaEvolve paper achieved a sum of 2.635 for n=26.

## Evaluator

**IMPORTANT**: To evaluate a program, run the evaluator script directly:

```bash
python benchmarks/skills/circle_packing/scripts/evaluator.py --run_id <run_id> --task_id <task_id> --program_name <program_name>
```

The arguments are parsed from the skill invocation:
- `run_id`: The run identifier (e.g., "circle_packing_test")
- `task_id`: The task identifier (e.g., "task_001")
- `program_name`: The program file name (e.g., "success.py")

The evaluator will:
1. Load the program from `benchmarks/outputs/<run_id>/<task_id>/<program_name>`
2. Execute `run_packing()` and validate the result
3. Save results to `benchmarks/outputs/<run_id>/<task_id>/eval_result.json`
4. Print the result file path on success, error message on failure

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

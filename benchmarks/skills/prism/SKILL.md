---
name: prism
description: Use when the user wants to solve or evolve the Prism GPU model-placement benchmark.
argument-hint: [run_id] [task_id] [artifact_ref]
---

# Prism GPU Model Placement

Assign models to 80 GB GPUs to minimize the maximum KV-cache pressure ratio
while keeping every model within memory. The evaluator uses deterministic
synthetic test cases and maximizes the resulting fitness.

## Evaluator

Use the standard attempt-local artifact contract:

```text
main_artifact_name = solution.py
artifact_ref = runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/solution.py
```

Run only:

```bash
uv run python benchmarks/skills/prism/scripts/evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
```

The evaluator validates model identity, placement completeness, and GPU memory
constraints before recording the score.

## Program interface

The candidate must define:

```python
def compute_model_placement(gpu_num, models):
    ...
```

Return a mapping from GPU IDs to lists containing exactly the input model
objects. `initial_program.py` is the initial reference program; the naive
baseline is retained as `initial_program_naive.py` for comparison only.

## Solver-facing task prompt

The source SkyDiscover benchmark provides the following task guidance to the
solver:

You are an expert in model placement on GPUs. Improve the model placement
algorithm by improving the `compute_model_placement` function in the initial
program that places models on available GPUs.

The algorithm must minimize the maximum KVPR across all GPUs while ensuring
that models fit in the GPUs' memory. KVPR is KV-cache pressure for a GPU and
indicates how crowded that GPU is. For a specific GPU, compute it as:

`sum(model.req_rate / model.slo for model in models) / (GPU_MEM_SIZE - sum(model.model_size for model in models))`

The generated program should be as simple as possible and execute correctly
without errors.

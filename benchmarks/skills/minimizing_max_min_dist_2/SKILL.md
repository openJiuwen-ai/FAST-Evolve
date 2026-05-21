---
name: minimizing_max_min_dist_2
description: Minimizing max-min distance (2D, 16 points) - maximize squared min/max pairwise distance ratio. Beat the AlphaEvolve benchmark of 1/12.889266112.
argument-hint: [run_id] [task_id] [artifact_ref]
---

# Minimizing Max-Min Distance (2D, 16 Points)

Design an explicit deterministic constructor for 16 points in 2D, maximizing:

```text
min_max_ratio = (minimum pairwise distance / maximum pairwise distance) ** 2
```

The reference benchmark is `1 / 12.889266112`; evaluator fitness is
`min_max_ratio / benchmark`, so higher is better and values above `1.0` beat
the reference.

## Evaluator

Run exactly:

```bash
uv run python benchmarks/skills/minimizing_max_min_dist_2/scripts/evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
```

Evaluator success means the candidate is valid and writes `"status":
"success"`. The benchmark value is not a pass/fail threshold.

## Program Interface

Implement:

```python
def min_max_dist_dim2_16() -> np.ndarray:
    ...
```

Return a numpy-compatible array of shape `(16, 2)`.

## Guidance

The objective is invariant to translation, rotation, and scaling. Avoid
duplicate points and arrangements with one tiny nearest-neighbor distance.

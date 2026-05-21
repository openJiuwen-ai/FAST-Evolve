---
name: heilbronn_convex_13
description: Heilbronn convex region problem (13 points) - maximize the minimum triangle area normalized by convex hull area. Beat the AlphaEvolve benchmark of 0.030936889034895654.
argument-hint: [run_id] [task_id] [artifact_ref]
---

# Heilbronn Convex Region (13 Points)

Design an explicit deterministic constructor for exactly 13 points in a convex
region, maximizing:

```text
min_area_normalized = smallest triangle area among all triples / convex hull area
```

The reference benchmark is `0.030936889034895654`; evaluator fitness is
`min_area_normalized / benchmark`, so higher is better and values above `1.0`
beat the reference.

## Evaluator

Run exactly:

```bash
uv run python benchmarks/skills/heilbronn_convex_13/scripts/evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
```

Evaluator success means the candidate is valid and writes `"status":
"success"`. The benchmark value is not a pass/fail threshold.

## Program Interface

Implement:

```python
def heilbronn_convex13() -> np.ndarray:
    ...
```

Return a numpy-compatible array of shape `(13, 2)`.

## Guidance

Use deterministic coordinates. Avoid duplicate points, near-collinear triples,
and degenerate convex hulls. Good constructions often balance boundary and
interior points and may exploit 3-fold rotational symmetry.

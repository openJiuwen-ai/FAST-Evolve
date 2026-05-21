---
name: hexagon_packing_11
description: Hexagon packing problem (11 unit hexagons) - pack unit regular hexagons inside a larger regular hexagon and maximize 1/outer side length. Beat the AlphaEvolve benchmark of 1/3.930092.
argument-hint: [run_id] [task_id] [artifact_ref]
---

# Hexagon Packing (11 Unit Hexagons)

Design an explicit deterministic packing of 11 unit regular hexagons inside a
larger regular hexagon, maximizing:

```text
inv_outer_hex_side_length = 1 / outer_hex_side_length
```

The reference benchmark is `1 / 3.930092`; evaluator fitness is
`inv_outer_hex_side_length / benchmark`, so higher is better and values above
`1.0` beat the reference.

## Evaluator

Run exactly:

```bash
uv run python benchmarks/skills/hexagon_packing_11/scripts/evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
```

Evaluator success means all inner hexagons are non-overlapping, fully contained,
and the result writes `"status": "success"`. The benchmark value is not a
pass/fail threshold.

## Program Interface

Implement:

```python
def hexagon_packing_11():
    ...
```

Return:

```text
inner_hex_data: shape (11, 3), rows are (x, y, angle_degrees)
outer_hex_data: shape (3,), (x, y, angle_degrees)
outer_hex_side_length: positive float
```

## Guidance

The evaluator uses polygon separation tests with tolerance, so exact tangencies
can fail. Leave a tiny numerical margin between hexagons and container sides.

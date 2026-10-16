---
name: cloudcast
description: Use when the user wants to solve or evolve the Cloudcast multi-cloud broadcast routing benchmark.
---

# Cloudcast Multi-Cloud Broadcast Optimization

Evolve a routing algorithm that broadcasts data from one cloud region to several
destinations at minimum total transfer cost. The evaluator runs the candidate on
five network configurations and maximizes the normalized fitness
`1 / (1 + total_cost)`.

## Evaluator

Use the standard attempt-local artifact contract:

```text
main_artifact_name = solution.py
artifact_ref = runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/solution.py
```

In the commands below, `<skill_dir>` is the directory containing this SKILL.md.

Run only the benchmark evaluator command:

```bash
python <skill_dir>/scripts/evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
```

The evaluator writes the normalized result with `status`, `artifact_ref`,
`fitness`, `metrics`, `profile`, and `misc` fields. A successful evaluation has
exit code 0 and `status: "success"`.

## Program interface

The candidate must define:

```python
def search_algorithm(src, dsts, G, num_partitions):
    ...
```

It must return a `BroadCastTopology`-compatible object whose paths contain every
destination and every partition, with contiguous graph edges carrying the
original edge data. `initial_program.py` is the initial reference program.

## Optimization objective and guidance

You are an expert in cloud infrastructure optimization. Your task is to evolve
the `search_algorithm(src, dsts, G, num_partitions)` function to minimize overall
data transfer cost across multiple clouds.

Focus on efficiently broadcasting input data to multiple destination nodes by
leveraging parallel paths and overlapping transfers across networks. Use the
`BroadCastTopology` class and `make_nx_graph` function to identify low-cost
routes. Prioritize strategies that reduce redundant transfers, balance load
across networks, and exploit multi-network topologies to minimize cost.

## Required data

The following source-controlled-by-download files are required under this skill:

- `profiles/cost.csv`
- `profiles/throughput.csv`
- `examples/config/intra_aws.json`
- `examples/config/intra_azure.json`
- `examples/config/intra_gcp.json`
- `examples/config/inter_agz.json`
- `examples/config/inter_gaz2.json`

Download them once with:

```bash
python <skill_dir>/scripts/download_data.py
```

The evaluator uses only the benchmark data and the candidate artifact.

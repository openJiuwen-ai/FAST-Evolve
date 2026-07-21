---
name: eplb
description: Use when the user wants to solve or evolve the Expert Parallelism Load Balancer benchmark for MoE inference.
argument-hint: [run_id] [task_id] [artifact_ref]
---

# Expert Parallelism Load Balancer

Evolve the placement and replication policy for Mixture-of-Experts inference.
The objective combines expert-load balance with the runtime of the rebalancing
algorithm; higher normalized fitness is better.

## Evaluator

Use the standard attempt-local artifact contract:

```text
main_artifact_name = solution.py
artifact_ref = runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/solution.py
```

Run only:

```bash
uv run python benchmarks/skills/eplb/scripts/evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
```

The evaluator writes the standard result JSON. Structural or runtime errors are
reported as failures; the numeric fitness remains the benchmark's raw score.

## Program interface

The candidate must define:

```python
def rebalance_experts(workload, num_replicas, num_groups, num_nodes, num_gpus):
    ...
```

It must return `(phy2log, log2phy, logcnt)` satisfying the replica-count,
non-negativity, shape, and next-workload coverage checks. `initial_program.py`
is the initial reference program.

The evaluator uses `NUM_REPLICAS=288`, `NUM_GROUPS=8`, `NUM_NODES=4`, and
`NUM_GPUS=32`.

## Optimization objective and guidance

You are an expert programmer specializing in optimization algorithms. Your task
is to improve the Mixture-of-Expert models Expert Parallelism Load Balancer
(MoE EPLB) expert rearrangement algorithm.

This algorithm takes load metrics recorded by the vLLM server and rearranges
the experts to balance the load. It can make replicas of some experts to
achieve better load balancing.

Your goals are two-fold:

1. Improve the algorithm to achieve better load balancing.
2. Improve the algorithm's efficiency by reducing its execution time, since
   perfect load balancing is NP-hard.

The current algorithm is implemented in the `rebalance_experts` function.

## Required data

Place the downloaded `expert-load.json` in this skill directory. It is not
checked into the repository because it is a large external workload file. The
evaluator reports a clear failure if the file is absent. Download it once with:

```bash
uv run python benchmarks/skills/eplb/scripts/download_data.py
```

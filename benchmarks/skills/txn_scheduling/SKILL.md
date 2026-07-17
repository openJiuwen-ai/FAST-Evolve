---
name: txn_scheduling
description: Use when the user wants to solve or evolve the transaction-scheduling benchmark for minimizing database makespan.
argument-hint: [run_id] [task_id] [artifact_ref]
---

# Transaction Scheduling

Evolve a transaction ordering for read/write workloads so that the simulated
total makespan is minimized. The evaluator preserves the original three
workloads and converts lower makespan into higher fitness.

## Evaluator

Use the standard attempt-local artifact contract:

```text
main_artifact_name = solution.py
artifact_ref = runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/solution.py
```

Run only:

```bash
uv run python benchmarks/skills/txn_scheduling/scripts/evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
```

The evaluator executes the candidate in an isolated subprocess with a bounded
timeout, validates each returned schedule, and writes the standard result JSON.
Its generated subprocess script uses escaped path literals so Windows paths are
handled safely.

## Program interface

The candidate must keep:

```python
def get_best_schedule(workload, num_seqs):
    ...
```

and the public `get_random_costs()` driver, which returns
`(makespan, [schedule_1, schedule_2, schedule_3])`. `initial_program.py` is the
initial reference program; `txn_simulator.py` and `workloads.py` are supporting
benchmark modules and are not candidate artifacts.

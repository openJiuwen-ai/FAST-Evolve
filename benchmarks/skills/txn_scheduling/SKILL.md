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

## Time contract

The three workload searches run serially in one candidate subprocess and share
one total 600-second budget. This budget is an algorithmic resource: a candidate
may allocate it dynamically and unevenly across workloads using their public
features. The initial program exposes `remaining_time_seconds()`, which returns
the safe remaining portion of that shared budget while the evaluator is running.

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

## Task definition and search guidance

You are an expert in database transaction optimization.

Only change code within `EVOLVE-BLOCK-START` and `EVOLVE-BLOCK-END`.

Your task is to improve a scheduling function for transactional workloads made
up of read and write operations on data items. Read-write and write-write
conflicts on the same items create dependencies between transactions, and
reducing the delay of these conflicts leads to schedules with lower makespan.
Focus on improving `get_best_schedule` to find a schedule with as low a
makespan as possible.

### Task and problem specifics

Improve `get_best_schedule` to find optimal transaction schedules that minimize
makespan for database workloads with read/write conflicts.

- Input is JSON containing transactions such as
  `"txn0":"w-17 r-5 w-3 r-4 r-54 r-14 w-6 r-11 w-22 r-7 w-1 w-8 w-9 w-27 r-2 r-25"`.
- Operations are reads (`r-{key}`) and writes (`w-{key}`) on data items.
- Read-write and write-write conflicts on the same key create dependencies.
- The goal is to find a transaction ordering that minimizes total makespan.

### Search suggestions

- A greedy algorithm may iteratively pick the transaction that increases
  makespan the least.
- Avoid relying only on heuristics such as transaction length or number of
  writes, because these do not correspond to the actual schedule makespan.

Focus on evolving `get_best_schedule` to produce the best possible schedule
with the lowest makespan. Explain step by step how the solution leads to a
better schedule.

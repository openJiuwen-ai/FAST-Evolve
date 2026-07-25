---
name: frontier_cs_algo
description: Use when the user wants to solve or evolve one Frontier-CS v1 algorithmic problem by numeric problem ID.
argument-hint: [problem_id]
---

# Frontier-CS Algorithmic Track

Evolve a C++17 solution for exactly one problem from the 172-problem
Frontier-CS v1 algorithmic track. A run must keep the same problem ID for every
task so that all candidates in the run have comparable fitness.

## Required problem selection

Read the numeric `problem_id` from the original task description. Supported
forms include:

```text
/agent_evolve_plugin:evolve frontier_cs_algo problem_id=0
/agent_evolve_plugin:evolve frontier_cs_algo 0
```

Do not infer the Frontier-CS problem from the evolution `task_id`, and do not
silently default to problem 0. Validate the selected ID against
`benchmarks/skills/frontier_cs_algo/suite_manifest.json`. The IDs are
non-contiguous and range beyond 171.

Prefer the explicit `problem_id=<digits>` form. Otherwise accept only the
numeric token immediately following `frontier_cs_algo`. If both forms are
present they must agree; if selection is missing or ambiguous, stop instead of
choosing an ID.

After validation, read these public problem resources before designing a
candidate:

```text
problem_statement = benchmarks/skills/frontier_cs_algo/problems/<problem_id>/statement.txt
problem_metadata = benchmarks/skills/frontier_cs_algo/problems/<problem_id>/metadata.json
initial_program_reference = benchmarks/skills/frontier_cs_algo/problems/<problem_id>/initial_program.cpp
```

The statement and initial reference may be read by both initial exploration
and task execution agents. During ordinary exploration and task execution, do
not read the evaluator source or any judge-side test data, checker, interactor,
answer, or reference solution. The orchestrator's explicit meta-guidance
exception remains governed by the evolve skill.

## Candidate contract

Use the standard attempt-local artifact contract:

```text
main_artifact_name = solution.cpp
artifact_ref = runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/solution.cpp
```

The candidate must be a single self-contained C++17 program that reads from
standard input and writes to standard output. Follow the selected problem's
statement and metadata, including whether it is a default or interactive
problem and its time and memory limits.

## Evaluator

Run only:

```bash
uv run python benchmarks/skills/frontier_cs_algo/scripts/evaluator.py --run-id <run_id> --task-id <task_id> --problem-id <problem_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
```

The evaluator itself runs on the host and submits the C++ source to the
Frontier-CS judge HTTP API. It does not start or manage containers. The judge
defaults to `http://localhost:8081`; set `FRONTIER_CS_JUDGE_URL` before the run
to use a different already-running judge.

A completed judge result is a successful evolutionary attempt even when its
score is zero. Stop the attempt after evaluator success and record it through
MCP. Judge infrastructure errors, submission errors, compilation errors, and
evaluation timeouts are failures eligible for the normal debug-attempt flow.

The evolutionary fitness is the judge's bounded 0-100 `score`.
`scoreUnbounded` is not used or retained because it is not the benchmark's
canonical score and is not reliable across all problem interactors.

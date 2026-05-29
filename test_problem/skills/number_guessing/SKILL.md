---
name: number_guessing
description: Use when user wants to solve number_guessing problems, or execute number_guessing tasks.
argument-hint: [run_id] [task_id] [artifact_ref]
---

# Number Guessing Problem

Guess a secret integer between 1 and 100000 (inclusive). The goal is to find the exact number.

## CRITICAL RULES (NEVER BREAK THESE)

1. **NEVER READ OR EXAMINE verify.py**: The file `verify.py` contains the secret answer. Reading it is cheating and produces an invalid result. You must NOT:
   - Use the `Read` tool on `verify.py` or any path ending in `verify.py`
   - Use `Grep` or `Glob` to search for content inside `verify.py`
   - Use Bash commands like `cat`, `head`, `tail`, `type`, `less`, `more`, `python -c "open(...)"` to display or extract `verify.py` contents
   - Use any other tool or method to inspect, view, or extract the source code of `verify.py`
   The ONLY allowed interaction with `verify.py` is **running it as a script** with the command below.

2. **ONLY CALL verify.py AS A SCRIPT**: Run it via Bash with the exact command below. Do not import it, do not execute parts of it, do not inspect its logic.

3. **GUESS WITHOUT KNOWING THE SECRET**: Your guess must be derived from reasoning, parent/inspiration strategies, or intuition — never from information obtained by reading `verify.py`.

## Evaluator

When this benchmark is used by `task-executor`, use the standard attempt-local artifact contract:

```text
artifact_ref = runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/guess.txt
```

For each attempt, write the candidate guess to that exact `artifact_ref`.
The `attempt_id` comes from the task-executor workflow and must be one of
`attempt_001`, `attempt_002`, or `attempt_003`.

**IMPORTANT**: To evaluate, run the evaluator script directly:

```bash
python test_problem/skills/number_guessing/scripts/verify.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
```

The arguments are parsed from the skill invocation:
- `run_id`: The run identifier (e.g., "number_guessing_test")
- `task_id`: The task identifier (e.g., "task_001")
- `artifact_ref`: The attempt-local text file path, normally `runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/guess.txt`

The evaluator will:
1. Read the guess from `artifact_ref`
2. Validate the guess
3. Save results to the `--out` path
4. Print the result file path on success, error message on failure

## Task

Write a single integer between 1 and 100000 (inclusive) to `guess.txt`.
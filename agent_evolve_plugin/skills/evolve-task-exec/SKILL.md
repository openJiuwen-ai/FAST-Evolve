---
name: evolve-task-exec
description: This skill should be used when executing self-evolution tasks with parent selection, inspiration integration, multi-attempt generation, and verification.
---

Execute a single evolutionary task with strict, ordered steps.

First, confirm these critical identifiers from the context:
- task_id: Unique identifier for this task
- run_id: Unique identifier for the evolution run
- task_description: What the task should accomplish
- max_attempts: (Optional) Maximum number of attempts, defaults to 3 if not provided

CRITICAL: If task_id, run_id, or task_description are missing, ask immediately before proceeding.

Then execute the following steps in EXACT ORDER:

---

## Step 1: Select Parent
Call MCP tool `evolve_select_parent` with:
- run_id
- task_id

Store the result:
- parent_id: May be null if no parent exists
- artifact_ref: Path to parent artifact file (may be null)
- fitness: Parent fitness score (may be null). Higher is better.

If parent exists, read the artifact file at `artifact_ref` to understand the parent solution.

---

## Step 2: Select Inspirations
Call MCP tool `evolve_select_inspirations` with:
- run_id
- task_id

Store the result as a list of inspirations, each with:
- inspiration_id
- artifact_ref
- fitness: Inspiration fitness score. Higher is better.

Read each inspiration artifact file to understand their approaches.
When comparing multiple inspirations, prefer higher-fitness artifacts and avoid
copying strategies from lower-fitness artifacts when they conflict with better
references.

---

## Step 2.5: Read Direction (If Exists)

Check for file: `runs/<run_id>/tasks/<task_id>/direction.txt`

If the file exists:
- Read the content as `direction_hint`
- Reference this direction when generating the solution in Step 3.2

---

## Step 3: Attempt Loop (Up To MAX_ATTEMPTS Attempts)

Determine MAX_ATTEMPTS from context:
- If `max_attempts` is provided, use that value
- Otherwise, default to 3

Initialize `attempt_index = 1`.
Initialize `attempt_succeeded = false`.
Initialize `final_verification_result_ref = null`.

This is a retry loop, not a fixed MAX_ATTEMPTS-attempt workflow. Start with
`attempt_001`. Run `attempt_002` only if `attempt_001` fails. Run
`attempt_003` only if `attempt_002` also fails. Continue up to `attempt_<MAX_ATTEMPTS>`.
If any attempt succeeds, stop immediately and do not create or run later attempts.

The remaining attempt budget is only for debugging failures. It is never an
optimization budget. Do not run a later attempt to improve a solution after a
previous attempt has already passed verification.

Each attempt is atomic: generate/debug the artifact once, save it once, run the
verifier once, then either record success or move to the next attempt after
failure. Do not repeatedly overwrite the same attempt's artifact and rerun the
verifier to chase a better fitness score.

For the current attempt, derive the attempt id exactly as:

```text
attempt_id = f"attempt_{attempt_index:03d}"
```

Example:
- attempt_index = 1 -> attempt_id = attempt_001
- attempt_index = 2 -> attempt_id = attempt_002
- attempt_index = 3 -> attempt_id = attempt_003
- attempt_index = 10 -> attempt_id = attempt_010

Use `attempt_id`, not `attempt_index`, in every filesystem path.

Continue this loop only while the current attempt failed and
`attempt_index < MAX_ATTEMPTS`.

Before starting any attempt after the first (attempt_002, attempt_003, ...), read the previous attempt's
`eval_result.json` if it exists. If the previous result has
`"status": "success"`, set:
- `attempt_succeeded = true`
- `final_verification_result_ref` to that previous `eval_result.json`

Then skip directly to Step 4. Do not create the current attempt directory, do
not write a new solution, and do not run the verifier again.

### 3.1 Create Attempt Directory
Use `mkdir -p` to create the directory at:
```
runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/
```
Use the exact path with correct run_id, task_id, attempt_id.

### 3.2 Generate or Debug Solution

**Evolution Strategy**:
The parent solution is the foundation of this evolution - use it as the primary reference for improvement and optimization; inspirations can be used as supplementary references for crossover, mutation, and borrowing optimization ideas.
Fitness is the task-generic quality score and higher is always better. Use the
parent as the primary reference, and use inspiration fitness to rank secondary
references. Borrow more from higher-fitness inspirations; treat lower-fitness
inspirations as optional contrastive examples rather than templates to copy.

**For attempt 1**:
Based on:
- Parent (if exists) - PRIMARY REFERENCE, build upon it
- Inspirations (if any) - SECONDARY REFERENCE, borrow ideas from them
- Task description
- Direction hint (if exists) - Consider this exploration direction

Generate a complete solution to the task. Use available skills as needed to complete the work.
Do not run local search by repeatedly editing and verifying `attempt_001`.
Internal optimization code inside the artifact is allowed, but the agent
workflow itself must produce one candidate artifact for this attempt and then
verify it once.

**For attempts 2 and beyond (debug mode)**:
DO NOT generate a completely new solution. Instead:
- Reuse the SAME parent and inspirations (they do NOT change between attempts)
- Take the previous attempt's output from the previous attempt directory:
  - `attempt_001` when the current attempt is `attempt_002`
  - `attempt_002` when the current attempt is `attempt_003`
  - `attempt_<n-1>` when the current attempt is `attempt_<n>`
- Analyze the verifier error output from the previous attempt
- Debug and fix the specific issues - make minimal, targeted changes to fix the errors
- Use the same overall approach, just correct the bugs

### 3.3 Save Output
Save ALL output files to:
```
runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/
```

If the task or benchmark skill defines a main artifact file name, save that
artifact inside this attempt directory. For example, a Python solution should
normally be saved as:

```text
runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/solution.py
```

This full path is the `artifact_ref` for the verifier and for the MCP record.
After saving the main artifact for this attempt, do not overwrite it unless the
verifier for this same attempt fails and you are preparing the next attempt in a
new attempt directory.

### 3.4 Run Verifier
Execute the verification method specified in the task description or skill:

Verifier success is defined only by the verifier result, not by whether the
fitness reaches a target or feels improvable. Exit code 0 or result JSON
`"status": "success"` means SUCCESS for this task attempt.

The verifier command is the only Bash command you should run for an attempt.
Do not run exploratory environment probes such as `python -c "import ..."` or
extra ad-hoc scripts. If a dependency is unavailable or a command is denied,
use the verifier result/error from the normal verifier command and continue the
attempt workflow.

- If verifier returns exit code 0: SUCCESS
  - Read the verification result JSON path from stdout
  - If stdout does not contain a path but the standard file exists, use:
    `runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json`
  - Set `attempt_succeeded = true`
  - Set `final_verification_result_ref` to the verification result JSON path
  - Stop the attempt loop immediately and proceed to Step 4
  - Do not create later attempts after a successful attempt
  - Do not edit the artifact or rerun the verifier to improve the score after
    this success
- If verifier returns non-zero exit code: FAILURE
  - Capture ALL error output from stdout/stderr - you will need this for debugging the next attempt
  - If attempt_index < MAX_ATTEMPTS: increment attempt_index and continue to next attempt
  - If attempt_index == MAX_ATTEMPTS: proceed to Step 5

---

## Step 4: Record Attempt (On Success Only)
ONLY if `attempt_succeeded == true`:

Call MCP tool `evolve_record_attempt` with:
- run_id
- task_id
- verification_result_ref: `final_verification_result_ref`

After this MCP call returns, proceed directly to Step 5. Do not generate,
debug, verify, or improve another attempt for this task. This is the only MCP
record call for this task.

---

## Step 5: End
Report only this exact line:
```
Task <task_id> completed
```
Replace `<task_id>` with the actual task_id.

Do NOT report any other details - success or failure, both count as completed.

---

## CRITICAL RULES (NEVER BREAK THESE)

1. **Order Matters**: Steps must execute in the exact order listed.
2. **Count Carefully**: At most MAX_ATTEMPTS attempts. Start with `attempt_001`; only run `attempt_002` after `attempt_001` fails, and continue up to `attempt_<MAX_ATTEMPTS>`.
3. **Remember IDs**: Always use the correct task_id, run_id, attempt_id in paths and tool calls.
4. **Stop on Success**: If verifier passes on any attempt, do NOT make more attempts - record and exit immediately.
5. **Read Artifacts**: Always read parent and inspiration files before generating.
6. **Verification First**: Only call `evolve_record_attempt` AFTER verifier succeeds with exit code 0.
7. **Minimal Output**: At the end, only report "Task <task_id> completed" - nothing else.
8. **Same Parent/Inspirations**: Parent and inspirations are selected ONCE at the beginning - they NEVER change between attempts.
9. **Debug, Don't Regenerate**: Attempts 2 and beyond are for DEBUGGING the previous attempt, not generating a brand new solution. Fix the specific errors while keeping the same overall approach.
10. **Task-Level Success Wins**: If any earlier attempt for this task has `eval_result.json` with `"status": "success"`, the task is successful. Do not run or create any later attempt.
11. **One Verifier Run Per Attempt**: Within a single attempt directory, write the artifact once and run the verifier once. A valid but suboptimal fitness is still success, not permission to keep editing the same attempt.
12. **No Environment Probing**: Do not run arbitrary Bash commands to inspect installed packages, files, or runtime capabilities. Use only the benchmark verifier command for execution feedback.
13. **NEVER READ VERIFIER SCRIPTS**: The task/benchmark skill may define a verifier script (e.g. `verify.py`). These scripts may contain secret answers or evaluation logic. You must NOT read, examine, or inspect them using any tool (Read, Grep, Glob, Bash `cat/head/tail/type`, etc.). The ONLY allowed interaction is **running them as a script** with the exact command specified in the task skill.

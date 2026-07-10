---
name: evolve
description: Triggered when the user asks to evolve, optimize, search, or iteratively improve something. Starts the self-evolution loop.
---

# Self-Evolution Master Skill

You are now running the **self-evolution master skill**, the orchestrator. Your responsibilities are: initialize a run, maintain the task queue, react to MCP convergence/meta-evolve actions, stop replenishment only on hard budget/external stops, wait for already launched tasks to finish, and call Evolution Controller MCP to generate a deterministic run summary report.

## Orchestrator Boundary Constraint

The orchestrator only handles queue scheduling and MCP calls. It must not read project files, invoke other skills, or get involved in task content, except for the explicit meta-guidance generation step below. During meta-guidance generation only, the orchestrator may read the current-best candidate code and verifier/evaluator source to understand scoring logic, then must return to scheduling. All normal task domain logic is executed by subagent agents in their own isolated context — they are self-contained and know how to find and read all necessary context themselves.

### Dispatch Template Constraint (ALL subagent dispatches)

The dispatch prompt MUST be produced by filling in the appropriate template below — **no other text is allowed**. Do not add, remove, or rearrange lines. Do not insert explanatory prose before, after, or between the parameter lines. Each `<placeholder>` is replaced with its actual value; everything else stays literal.

**Template for init-explorer dispatch:**
```
run_id: <run_id>
task_description: <task_description>
num_directions: <num_directions>
```

**Template for task-executor dispatch:**
```
run_id: <run_id>
task_id: <task_id>
task_description: <task_description>
max_attempts: <max_attempts>
```

These templates are **exclusive** — they define the exact and complete content of the dispatch prompt. Any deviation is a constraint violation, including but not limited to:

- Adding workflow instructions ("first read X, then do Y")
- Adding format specifications ("output as JSON with fields A, B, C")
- Adding rationale or strategy guidance ("focus on diverse approaches")
- Adding role descriptions ("You are the init-explorer agent...")
- Adding any prose, commentary, or context that is not a parameter line
- Reordering lines or changing the key names

### `task_description` Fidelity Constraint

`task_description` must be identical to the user's original input submitted to the skill, character by character. Any form of rewriting, expansion, supplementation, interpretation, translation, or inference is prohibited — even if you believe your version is clearer, more complete, or easier for the subagent to understand.

### `max_attempts` Dispatch Constraint

`max_attempts` MUST be explicitly included in every task-executor dispatch. It is **never** omitted or left to default. Initial-fill tasks use `max_attempts = 10`; replenishment tasks use `max_attempts = 3`. This distinction is essential: initial tasks explore from scratch and need more attempts, while replenishment tasks build on parent/inspiration context and need fewer.

## Architecture Roles

| Component | Responsibility |
|---|---|
| This skill (orchestrator) | Initialize run, maintain task queue, handle MCP `action`, generate requested meta-guidance JSON, finalization |
| task-executor agent | Execute the full lifecycle of individual tasks |
| Evolution Controller MCP | Parent/inspiration selection, attempt recording, convergence/meta-guidance state, run summary generation |

---

## Step 1: Run Initialization

1. Generate `run_id`: `run_YYYYMMDDHHMMSS`.
   - Use the current local start time with second-level precision. Use the Bash tool to execute `date +%Y%m%d%H%M%S`.
2. Determine `max_total_tasks` from the user's request:
   - If the user specified an iteration count (e.g., "200 iterations", "200 tasks", "max 200", "run 200 rounds"), extract that number.
   - If the user did not specify, default to 50.
   - Call `evolve_configure_run(run_id, max_total_tasks=<value>, task_description=<original user input>)` BEFORE any other `evolve_*` call for this run.
3. Create the run workspace directory and persistent state files. **All `runs/` paths are relative to the project root (cwd), not to the skill's base directory.** Use Bash to execute:

   ```bash
   mkdir -p "runs/<run_id>" && touch "runs/<run_id>/completed_tasks.jsonl"
   ```

   Then write `scheduler_state.json` using the Write tool:

   ```json
   {"run_id": "<run_id>", "running_tasks": []}
   ```

   Write path: `runs/<run_id>/scheduler_state.json`.

   - `completed_tasks.jsonl`: subagents append to this file on completion; the orchestrator reads it. Pre-creating the empty file avoids the first subagent needing to handle a missing-file case.
   - `scheduler_state.json`: only the orchestrator reads/writes this file. It is used to recover running-task knowledge after context compression, and to reconcile against completed_tasks.jsonl to discover tasks whose notifications were lost.

4. Confirm with the user and start.

---

## Step 1.5: Generate Initial Exploration Directions

Invoke the `agent_evolve_plugin:init-explorer` agent using the **init-explorer dispatch template** (see Dispatch Template Constraint in Orchestrator Boundary Constraint section). Fill in:
- `run_id`: the current run_id
- `task_description`: the user's original input verbatim
- `num_directions`: 4 (matches MAX_CONCURRENT_TASKS)

Wait for `agent_evolve_plugin:init-explorer` to complete before proceeding to Step 2.

---

## Step 2: Task Queue Scheduling

Maintain the task queue with at most **4 concurrent tasks**. Task completion data is persisted in `completed_tasks.jsonl`, and notifications merely prompt the orchestrator to read the file.

### Data Flow

All `runs/` paths below are relative to the project root (cwd).

```
runs/<run_id>/
├── completed_tasks.jsonl          ← subagents append, orchestrator(you) reads
├── scheduler_state.json           ← only orchestrator(you) reads/writes
```

### Scheduling Pseudocode

```text
MAX_CONCURRENT_TASKS = 4
task_counter = 0
running_tasks = []
stop_requested = false

// Initial fill
FOR i = 1..MAX_CONCURRENT_TASKS:
  task_counter += 1
  task_id = f"task_{task_counter:03d}"
  Dispatch to task-executor using the task-executor dispatch template (fill: run_id, task_id, task_description, max_attempts=10)
  running_tasks.append(task_id)
  Update running_tasks in scheduler_state.json

// Event loop
WHILE running_tasks is not empty:
  // Discover completed tasks
  Wait for any task-executor notification to arrive
  1. Load running_tasks from scheduler_state.json, ALWAYS trust the list from the file for the current state
  2. Read completed_tasks.jsonl, parse each line (skip unparseable lines)
  3. Find all task_ids present in completed_tasks.jsonl but still in running_tasks
     → these are completed tasks
  4. For each newly discovered completed task:
     - Extract fitness, status, artifact_ref from its JSONL entry
     - Update the Progress Update table with this info (see Progress Update Table section below)
  5. Remove these completed task_ids from running_tasks and update scheduler_state.json

  // Convergence/meta-evolve check & replenishment
  FOR each newly completed task:
    convergence = evolve_check_convergence(run_id)
    IF convergence.action == "budget_exhausted":
      stop_requested = true
      BREAK (stop convergence-check loop)
    IF convergence.action == "request_meta_guidance":
      request = evolve_get_meta_guidance_request(run_id, request_id=convergence.request_id)
      Generate concise structured guidance JSON from request only.
      Call evolve_commit_meta_guidance(run_id, request.request_id, guidance_json)
      Continue scheduling; do not stop.

  IF stop_requested:
    // Stop launching new tasks, continue waiting for remaining running tasks' notifications
    CONTINUE (back to WHILE, wait for all tasks to complete)

  // Replenish: each completed task frees one slot, immediately fill it
  FOR each newly completed task (when stop_requested == false):
    task_counter += 1
    task_id = f"task_{task_counter:03d}"
    Dispatch to task-executor using the task-executor dispatch template (fill: run_id, task_id, task_description, max_attempts=3)
    running_tasks.append(task_id)
    Update scheduler_state.json

END WHILE

// After all launched tasks finish, do finalization (see Step 3)
```

### scheduler_state.json Format

```json
{
  "run_id": "run_20260601204625",
  "running_tasks": ["task_005", "task_002"]
}
```

### completed_tasks.jsonl Format

Each line is one JSON object, appended by the subagents on completion:

```jsonl
{"task_id":"task_005","fitness":0.7709,"status":"success","artifact_ref":"runs/<run_id>/tasks/task_005/attempts/attempt_001/solution.py"}
{"task_id":"task_002","fitness":"-","status":"failure","artifact_ref":"runs/<run_id>/tasks/task_002/attempts"}
```

While parsing, skip unparseable lines.

### Progress Update Table

During the run, maintain a concise terminal `Progress Update` whenever a task completes, a replacement task is launched, or the workflow is waiting for remaining running tasks. Use a Unicode box-drawing table and keep status emoji. The table must include exactly these semantic columns: `Task`, `Status`, `Fitness`, and `Artifact`; do not use a `Note` column. **All values for completed tasks are extracted from completed_tasks.jsonl during the event loop. Running tasks show `—` for Fitness and Artifact.** Status mapping: `success` → `✅ Completed`, `failure` → `⚠️ Failed`, running task → `🔄 Running`. **Display fidelity rules: fitness values must be shown exactly as they appear in completed_tasks.jsonl — never round, truncate, or reformat them; artifact_ref paths must be shown in full — never abbreviate, shorten, or truncate them.** After the table, print one short line with the current best fitness and number of running tasks.

Fitness is always maximized. `Best fitness` MUST be the largest successful
numeric fitness from `completed_tasks.jsonl`, or
`convergence.diagnostics.current_best_fitness` when available. Do not infer the
direction from raw benchmark metrics such as loss, c3, runtime, or distance.

### Critical Constraints

1. Once `action == "budget_exhausted"`, stop launching new tasks.
2. If tasks are still running, wait for all of them to complete before ending the run.
3. After all launched tasks have completed, you must call `evolve_finalize_run(run_id)` exactly as the finalization step.
4. `evolve_finalize_run` returns report refs. Do not paste the full report into the conversation context.
5. Do not let a freed slot sit idle while `action != "budget_exhausted"`: for every single completed task, immediately launch exactly one replacement task after the convergence check.
6. Success and failure both count as task completion. A failed task still frees its slot and should be replaced unless `action == "budget_exhausted"`.
7. scheduler_state.json is written only by the orchestrator. Every modification must be immediately persisted to disk via Write.
8. Every dispatch to task-executor MUST include `max_attempts`. Initial-fill tasks: `max_attempts = 10`. Replenishment tasks: `max_attempts = 3`. Never omit this parameter.
9. Plateau, WATCHING, `request_meta_guidance`, and failed meta-guidance generation are NOT stop signals.

### Meta-Guidance Generation

When MCP returns `action == "request_meta_guidance"`, generate exactly one JSON object in this shape and submit it with `evolve_commit_meta_guidance`:

```json
{
  "guidance": [
    {
      "idea": "Short tactic title",
      "description": "What to try and why",
      "target_metric": "fitness",
      "novelty_claim": "Why this differs from current lineages",
      "current_best_assessment": "What works in current_best, what may be trapping the search, and whether this guidance refines, departs from, or replaces that route",
      "relationship_to_current_best": "refine|depart|replace",
      "implementation_plan": ["Concrete step 1", "Concrete step 2"],
      "cautions": ["Risk or constraint"],
      "approach_type": "algorithm family or technique",
      "avoid_repeating": ["Failed or overused route"],
      "max_uses": 2
    }
  ]
}
```

Use the MCP request payload, the original task description already in context,
and the current-best candidate implementation referenced by
`request.current_best.artifact_ref` or embedded in
`request.current_best.artifact_excerpt`. This candidate code is allowed context.
You may also read verifier/evaluator source during this meta-guidance
generation step, aligned with AdaEvolve's paradigm-generation setup. Use it only
to understand scoring logic, constraints, and failure modes. Do not modify
verifier/evaluator source, bypass the evaluator, or hard-code evaluator-specific
shortcuts into the guidance.

Before writing the JSON, follow `request.generation_instructions` if present.
In particular, critically assess the current best instead of assuming it is the
right route. It may be a local optimum or wrong search family. Use
`notes_summary` and `guidance_history` to avoid repeating strategies whose prior
outcomes showed no improvement.

Generate 1 to 3 guidance items. If generating multiple items, every
`approach_type` MUST be different; duplicate approach types will be rejected by
MCP. Prefer 3 distinct strategy families when the plateau evidence supports
multiple plausible escapes. Do not add prose outside the JSON object. Keep each
item concrete enough for task-executor to implement.

---

## Step 3: Run Finalization

### 3.1 Final Progress Table Update

After all running_tasks are empty, perform one final progress table update:

1. Read `completed_tasks.jsonl`, parse all lines
2. Update the Progress Update table to its final state

### 3.2 Final Convergence Check & Finalize

```text
evolve_check_convergence(run_id)    // let MCP run_state reflect the final state
finalize_result = evolve_finalize_run(run_id)
```

### 3.3 Report

On normal stop, report only:

```text
Run finalized.
report_ref: <finalize_result.report_ref>
summary_ref: <finalize_result.summary_ref>
```

Do not write a long run summary yourself. The deterministic report is written by MCP to:

```text
runs/<run_id>/run_summary.md
runs/<run_id>/run_summary.json
```

---

## Error Handling

| Situation | Action |
|---|---|
| MCP tool returns error | Retry once; if persistent, stop launching new tasks, wait for already launched tasks to complete, then call `evolve_finalize_run(run_id, stop_reason="mcp_error")` |
| task-executor fails | Still call `evolve_check_convergence`; MCP decides whether to stop or continue from recorded attempts |
| User interrupts | Stop launching new tasks; if possible, wait for launched tasks to complete; then call `evolve_finalize_run(run_id, stop_reason="user_interrupted")` |

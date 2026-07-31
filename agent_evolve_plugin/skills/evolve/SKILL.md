---
name: evolve
description: Triggered when the user asks to evolve, optimize, search, or iteratively improve something. Starts the self-evolution loop.
---

# Self-Evolution Master Skill

You are now running the **self-evolution master skill**, the orchestrator. Your responsibilities are: initialize a run, maintain the task queue, react to MCP convergence/meta-evolve actions, delegate requested meta-guidance generation, stop replenishment only on hard budget/external stops, wait for already launched tasks to finish, and call Evolution Controller MCP to generate a deterministic run summary report.

## Orchestrator Boundary Constraint

The orchestrator only handles queue scheduling, compact MCP control-plane calls,
and exact-template agent dispatches. It must not read task-content project
files, invoke task-content skills, retrieve a pending meta-guidance request,
generate guidance, inspect the current-best candidate or verifier/evaluator, or
commit guidance. Initial exploration, task execution, and meta-guidance
generation are executed by dedicated agents with separate conversation contexts
but one shared main-repository working directory.

### Dispatch Template Constraint (ALL subagent dispatches)

The dispatch prompt MUST be produced by filling in the appropriate template below — **no other text is allowed**. Do not add, remove, or rearrange lines. Do not insert explanatory prose before, after, or between the parameter lines. Each `<placeholder>` is replaced with its actual value; everything else stays literal.

### Main-Repository Dispatch Constraint (ALL subagent dispatches)

Every dispatch to `agent_evolve_plugin:init-explorer`,
`agent_evolve_plugin:task-executor`, or
`agent_evolve_plugin:meta-guidance-generator` MUST run in the main repository
working directory. The `Agent` input MUST NOT contain an `isolation` field:
omit it entirely, rather than setting `"worktree"`, `"remote"`, or any other
isolation mode. All persistent artifacts,
including `runs/<run_id>/...`, MUST be read from and written to the main
repository, never to `.claude/worktrees/...`.

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

**Template for meta-guidance-generator dispatch:**
```
run_id: <run_id>
request_id: <request_id>
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
| This skill (orchestrator) | Initialize run, maintain task queue, route MCP `action`, dispatch meta-guidance requests by ID, finalization |
| meta-guidance-generator agent | Fetch one pending request, inspect generation evidence, generate and commit guidance, return a compact result |
| task-executor agent | Execute the full lifecycle of individual tasks |
| Evolution Controller MCP | Parent/inspiration selection, attempt recording, scheduler-state persistence, convergence/meta-guidance state, run summary generation |

---

## Step 1: Run Initialization

1. Derive `task_slug` from the user's original input.
   - When the input contains the word `benchmark`, use the nearest preceding ASCII snake-case identifier as the benchmark name. For example, use `minimizing_max_min_dist_3` for `run the minimizing_max_min_dist_3 benchmark end to end.`.
   - For the compact `/evolve <identifier>` form, use that explicit identifier; for example, `/evolve circle_packing` uses `circle_packing`.
   - Otherwise derive a concise descriptive label for the task target.
   - Normalize the label to lowercase ASCII snake case using only `[a-z0-9_]`, trim leading/trailing underscores, and limit it to 64 characters. If normalization produces an empty label, use `task`.
   - `task_slug` is only a run identifier component. It must not change the original input passed as `task_description`.
2. Generate `run_id`: `<task_slug>_YYYYMMDDHHMMSS`.
   - Use the current local start time with second-level precision. Use the Bash tool to execute `date +%Y%m%d%H%M%S`.
3. Determine `max_total_tasks` from the user's request:
   - If the user specified an iteration count (e.g., "200 iterations", "200 tasks", "max 200", "run 200 rounds"), extract that number.
   - If the user did not specify, default to 50.
   - Call `configure_run(run_id, max_total_tasks=<value>, task_description=<original user input>)` BEFORE any other controller MCP call for this run.
   - Store `dashboard.url` from the response when available. Print exactly one high-visibility startup banner, with a blank line before and after it:

     ```text
     ╭────────────────────────────────────────────────────────────╮
     │ 🚀 AGENT EVOLVE UI                                        │
     │ 🖥️  <dashboard.url>                                        │
     ╰────────────────────────────────────────────────────────────╯
     ```

     Do not emit raw ANSI color codes because renderer support varies. The banner is printed only once, so its visibility does not create recurring context cost. If the dashboard did not start, print one short warning and continue; UI availability is never a reason to stop evolution.
4. Create the run workspace directory and persistent state files. **All `runs/` paths are relative to the project root (cwd), not to the skill's base directory.** Use Bash to execute:

   ```bash
   mkdir -p "runs/<run_id>" && touch "runs/<run_id>/completed_tasks.jsonl"
   ```

   - `completed_tasks.jsonl`: subagents append to this file on completion; the orchestrator reads it. Pre-creating the empty file avoids the first subagent needing to handle a missing-file case.
   - `scheduler_state.json`: `configure_run` initializes this file. The scheduler-state MCP tools update and read it without exposing full-file diffs in the terminal. It supports context-compression recovery and exposes the task-to-subagent mapping to the dashboard.

5. Confirm with the user and start.

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
├── scheduler_state.json           ← MCP-owned persistence; dashboard reads
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
  Dispatch to task-executor with `run_in_background: true` using the task-executor dispatch template (fill: run_id, task_id, task_description, max_attempts=10)
  Capture the exact subagent identifier returned by the dispatch operation
  update_task_status(run_id, task_id, status="running", agent_id=<returned_subagent_id>)
  running_tasks.append(task_id)

// Event loop
WHILE running_tasks is not empty:
  // Discover completed tasks
  Wait for any task-executor notification to arrive
  1. state = get_scheduler_state(run_id)
     Load running_tasks from state.running_tasks and ALWAYS trust this compact MCP response
  2. Read completed_tasks.jsonl, parse each line (skip unparseable lines)
  3. Find all task_ids present in completed_tasks.jsonl but still in running_tasks
     → these are completed tasks
  4. For each newly discovered completed task:
     - Extract fitness, status, artifact_ref from its JSONL entry
     - Call update_task_status(run_id, task_id, status="completed")
  5. Remove these completed task_ids from the local running_tasks list

  // Convergence/meta-evolve check & replenishment
  meta_requests_attempted_this_reconciliation = set()
  FOR each newly completed task:
    convergence = check_convergence(run_id)
    IF convergence.action == "budget_exhausted":
      stop_requested = true
      BREAK (stop convergence-check loop)
    IF convergence.action == "request_meta_guidance":
      request_id = convergence.request_id
      IF request_id is not in meta_requests_attempted_this_reconciliation:
        meta_requests_attempted_this_reconciliation.add(request_id)
        Dispatch to meta-guidance-generator with `run_in_background: false` using the meta-guidance-generator dispatch template (fill: run_id, request_id)
        Wait for the meta-guidance-generator compact result.
        post_meta = check_convergence(run_id)
        IF post_meta.action == "budget_exhausted":
          stop_requested = true
          BREAK (stop convergence-check loop)
        Treat post_meta as authoritative: a successful commit is confirmed when post_meta.meta_guidance.has_pending_request == false and post_meta.meta_guidance.active_count > 0, even if the worker's compact result was lost or stale.
        IF the worker result status is `mcp_error` and the authoritative postcondition does not confirm a commit:
          stop_requested = true
          Apply the persistent MCP error rule during finalization.
          BREAK (stop convergence-check loop)
        If the postcondition does not confirm a commit and the request remains pending, do not dispatch the same request_id again during this reconciliation pass.
      Continue scheduling; meta-guidance generation failure alone is not a stop signal.

  IF stop_requested:
    // Stop launching new tasks, continue waiting for remaining running tasks' notifications
    CONTINUE (back to WHILE, wait for all tasks to complete)

  // Replenish: each completed task frees one slot, immediately fill it
  FOR each newly completed task (when stop_requested == false):
    task_counter += 1
    task_id = f"task_{task_counter:03d}"
    Dispatch to task-executor with `run_in_background: true` using the task-executor dispatch template (fill: run_id, task_id, task_description, max_attempts=3)
    Capture the exact subagent identifier returned by the dispatch operation
    update_task_status(run_id, task_id, status="running", agent_id=<returned_subagent_id>)
    running_tasks.append(task_id)

  // Compact terminal visibility
  Emit at most one compact progress line after reconciliation and replenishment, and only when:
    - completed task count reaches a multiple of 5, OR
    - a new global best appears, OR
    - meta-guidance is requested/committed, OR
    - the run enters stopping/finalization
  Do not emit repeated waiting messages when no persisted state changed

END WHILE

// After all launched tasks finish, do finalization (see Step 3)
```

### scheduler_state.json Format

This is an MCP-owned on-disk format for the dashboard and recovery. Never inspect or modify it with `Read`, `Write`, `Edit`, or shell commands during a run; use `get_scheduler_state` and `update_task_status` instead.

```json
{
  "run_id": "circle_packing_20260601204625",
  "running_tasks": ["task_005", "task_002"],
  "task_agents": {
    "task_002": {"agent_id": "agent-7e15", "status": "running"},
    "task_005": {"agent_id": "agent-93b2", "status": "running"}
  }
}
```

### completed_tasks.jsonl Format

Each line is one JSON object, appended by the subagents on completion:

```jsonl
{"task_id":"task_005","fitness":0.7709,"status":"success","artifact_ref":"runs/<run_id>/tasks/task_005/attempts/attempt_001/solution.py"}
{"task_id":"task_002","fitness":"-","status":"failure","artifact_ref":"runs/<run_id>/tasks/task_002/attempts"}
```

While parsing, skip unparseable lines.

### Compact Terminal Progress

The dashboard is the detailed observability surface. Never print the Unicode progress table, completed artifact paths, Notes content, meta-guidance content, task-by-task history, or the full running-task list in the main terminal.

When one of the throttled visibility conditions in the pseudocode is met, print exactly one line in this shape:

```text
Progress <completed>/<max_total_tasks> · best <best_fitness_or_—> · <running_count> active · 🖥️ UI → <dashboard.url>
```

This line is a heartbeat, not a report. Do not print it more than once for the same completed count unless the run state changed (for example meta-guidance was committed or stopping began). While merely waiting for subagents, print nothing unless there has been no user-visible output for at least 60 seconds; in that case print only `Evolution is active · <running_count> agents · 🖥️ UI → <dashboard.url>`.

Fitness is always maximized. `best_fitness` MUST be the largest successful numeric fitness from `completed_tasks.jsonl`, or `convergence.diagnostics.current_best_fitness` when available. Do not infer direction from raw benchmark metrics such as loss, c3, runtime, or distance.

### Critical Constraints

1. Once `action == "budget_exhausted"`, stop launching new tasks.
2. If tasks are still running, wait for all of them to complete before ending the run.
3. After all launched tasks have completed, you must call `finalize_run(run_id)` exactly as the finalization step.
4. `finalize_run` returns report refs. Do not paste the full report into the conversation context.
5. Do not let a freed slot sit idle while `action != "budget_exhausted"`: for every single completed task, launch exactly one replacement task after the convergence/meta-guidance check and any required foreground meta worker has completed.
6. Success and failure both count as task completion. A failed task still frees its slot and should be replaced unless `action == "budget_exhausted"`.
7. Never inspect or modify `scheduler_state.json` with `Read`, `Write`, `Edit`, or shell commands. Persist every task start/completion immediately through `update_task_status`; recover active scheduling state through `get_scheduler_state`. These compact tools preserve completed task-agent mappings for UI history without printing full-file diffs.
8. Every task-executor dispatch MUST set `run_in_background: true` and include `max_attempts`. Initial fill: `max_attempts=10`; replenishment: `max_attempts=3`.
9. Plateau, WATCHING, `request_meta_guidance`, and failed meta-guidance generation are NOT stop signals.
10. The orchestrator must never retrieve the pending meta-guidance payload, read its artifact/evaluator evidence, generate the guidance JSON, or commit it. It passes only `run_id` and `request_id` to `agent_evolve_plugin:meta-guidance-generator`.
11. Meta-guidance-generator dispatch is foreground and stateless. Never persist its agent identifier in scheduler state, never poll it, never run more than one for the same `request_id` in one reconciliation pass, and always perform the compact convergence recheck before replenishment. The MCP recheck is authoritative if it conflicts with or outlives the worker result.
12. Never print or repeat the meta worker's request payload or guidance. Its compact result is the only worker content allowed in orchestrator context.

### Meta-Guidance Worker Dispatch

When MCP returns `action == "request_meta_guidance"`, invoke
`agent_evolve_plugin:meta-guidance-generator` with the exact dispatch template.
Pass only the two identifiers; the worker retrieves the persisted request and
commits its own output. Wait in the foreground so a successful commit becomes
active before any replacement task can call parent selection.

The worker returns only its compact result contract. Reconcile that claim with
one compact `check_convergence` call and treat the MCP response as the
source of truth. A non-commit leaves the request pending for a later completion
event; it does not stop task replenishment and must not cause a tight retry loop
in the current reconciliation pass.

---

## Step 3: Run Finalization

### 3.1 Final Compact Progress Update

After all running_tasks are empty, read `completed_tasks.jsonl` and emit one final compact progress line using the format below. Do not print a table or artifact paths.

### 3.2 Final Convergence Check & Finalize

```text
check_convergence(run_id)    // let MCP run_state reflect the final state
finalize_result = finalize_run(run_id)
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
| MCP tool returns error | Retry once; if persistent, stop launching new tasks, wait for already launched tasks to complete, then call `finalize_run(run_id, stop_reason="mcp_error")` |
| task-executor fails | Still call `check_convergence`; MCP decides whether to stop or continue from recorded attempts |
| meta-guidance-generator returns `not_committed`, fails to return its compact result, or fails generation | Keep the MCP request pending, continue replenishment, and allow one later completion event to retry it |
| meta-guidance-generator returns `mcp_error` after its own retry and the authoritative post-check does not confirm a commit | Apply the persistent MCP error rule above |
| User interrupts | Stop launching new tasks; if possible, wait for launched tasks to complete; then call `finalize_run(run_id, stop_reason="user_interrupted")` |

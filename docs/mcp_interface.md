# Evolution Controller MCP: Minimal-Context Interface

**English** | [中文](mcp_interface_CN.md)

This document is the public tool contract of `evotrias.mcp`, the Evolution
Controller MCP. Its goal is to keep agent context small: whatever the MCP can
recover from local state is never re-stated by the orchestrator or the
task-executor.

`evotrias.mcp` is a thin shell. `evotrias/mcp/public_tools.py` declares the
agent-facing contract (tool names, arguments, defaults, docstrings) and forwards
every call to `evotrias.evolve_ctrl.controller`, which runs the algorithms and
reads/writes run state through `evotrias.db`. This page describes the contract;
`controller.py` is the implementation.

## 1. First principles

1. Non-essential information never enters MCP inputs or outputs.
2. Long content is passed by ref, never inline.
3. Parent, inspirations, attempts, notes and meta-guidance state are all
   persisted locally by the MCP.
4. The orchestrator only schedules and makes compact control calls; content
   generation (meta-guidance, notes) happens in dedicated subagents with their
   own context.
5. A plateau never stops the run early; only `action = "budget_exhausted"`
   means "stop creating tasks".

## 2. Public tools

### `configure_run`

Input:

```python
configure_run(
    run_id: str,
    max_total_tasks: int = 50,
    lineage_root_number: int = 4,
    max_concurrent_tasks: int = 4,
    task_description: str = "",
)
```

Notes:

- Must be called before any other controller tool for the same run.
- `lineage_root_number` and `max_concurrent_tasks` are independent knobs. The
  former is the number of initial exploration directions and caps the total
  number of root tasks (no evolution task is created until every root has
  finished); the latter caps in-flight tasks in every phase. Neither is derived
  from the other; both default to 4 and are clamped to at least 1.
- The three numeric knobs are written to `config.json` together with every
  other run setting. Environment overrides (`AGENT_EVOLVE_*`; the list is in
  the top-level README under "Defaults and Runtime Switches") are resolved once
  at this moment and frozen into the file, so the persisted config is the
  complete configuration for the rest of the run.
- `task_description` is stored in `run_state.json` for later meta-guidance
  requests.
- Calling again for the same `run_id` is ignored and returns the locked
  configuration with `configured: false`, including when another process
  configured it.
- The optional dashboard is started here. Its status is reported under
  `dashboard` and never makes the run fail.

Response:

```json
{
  "configured": true,
  "message": "Run configured.",
  "max_total_tasks": 50,
  "lineage_root_number": 4,
  "max_concurrent_tasks": 4,
  "meta_evolve": {
    "enabled": true,
    "guidance_num_to_generate": 3,
    "guidance_max_uses": 2
  },
  "dashboard": {"enabled": true, "started": true, "url": "http://127.0.0.1:4000", "port": 4000}
}
```

### `select_parent`

Input:

```python
select_parent(run_id: str, task_id: str)
```

Output:

```json
{
  "parent_id": "task_003",
  "artifact_ref": "runs/run_001/tasks/task_003/attempts/attempt_001/solution.py",
  "fitness": 0.9415,
  "metrics": {"loss": 0.0585}
}
```

Semantics:

- The first call for a task samples a parent and caches it in task context.
- Repeated calls for the same `task_id` return the cached result without
  sampling again.
- **Root phase** (not every root task has finished yet): always returns
  `parent_id: null` and binds no meta-guidance. A root task starts from its own
  direction only, even if earlier roots already populated the success pool.
  `select_inspirations` then returns an empty list too.
- Otherwise, if active meta-guidance exists, the MCP binds it to the canonical
  best at call time and uses that as the parent; else the regular parent
  selector runs.
- An empty success pool yields `parent_id: null`.

### `select_inspirations`

Input:

```python
select_inspirations(run_id: str, task_id: str)
```

Output:

```json
{
  "inspirations": [
    {
      "inspiration_id": "task_002",
      "artifact_ref": "runs/run_001/tasks/task_002/attempts/attempt_001/solution.py",
      "fitness": 0.9352,
      "metrics": {"loss": 0.0648}
    }
  ]
}
```

Semantics:

- `select_parent` must have been called first.
- The parent is never repeated in this response.
- Returns id/ref/fitness/metrics only: no artifact content, profile or sketch.
- The chosen inspiration ids are written to `task_contexts.json` and stay fixed
  across retries of the same task.

### `get_notes_prompt`

Input:

```python
get_notes_prompt(run_id: str, task_id: str)
```

Semantics:

- Call after both parent and inspirations are fixed.
- Returns a ready-to-inject prompt section.
- When Notes is disabled, returns `available: false` with the reason.

### `get_meta_guidance_prompt`

Input:

```python
get_meta_guidance_prompt(run_id: str, task_id: str)
```

Semantics:

- Call after `select_parent`.
- If the task was assigned active guidance, returns a ready-to-inject
  meta-guidance prompt section.
- Otherwise returns `available: false`.
- The task-executor treats the prompt as a required strategy: when it conflicts
  with the usual parent/inspiration direction, meta-guidance takes priority, but
  it can never override the task requirements or the verifier output contract.

### `record_attempt`

Input:

```python
record_attempt(
    run_id: str,
    task_id: str,
    verification_result_ref: str,
    attempt_id: str = "",
    artifact_ref: str = "",
    max_attempts: int = 3,
)
```

What the MCP does:

1. Restores parent, inspirations and meta-guidance assignment from
   `task_contexts.json`.
2. Reads the JSON result at `verification_result_ref` (absolute or
   cwd-relative path).
3. Appends one record to `attempts.jsonl`. Recording is attempt-level, not
   task-level, because Notes needs failed attempts as evidence; repeated calls
   for the same task/attempt pair are idempotent.
4. On `status == "success"` with a numeric top-level `fitness`, marks the
   attempt `in_success_pool`. A task enters the success pool once: a later
   success for the same task is logged with a warning and does not enter the
   pool again.
5. On the task's final attempt (a success, or an attempt number that reaches
   `max_attempts`), records the meta-guidance outcome for later attribution.
6. Returns Notes requests for the task-executor to distill local experience.

`attempt_id` is resolved from the argument, then the `attempt_id` field of the
result JSON, then the parent directory of `verification_result_ref` when it is
named `attempt_*`, then `task_id`.

The JSON at `verification_result_ref` needs at least:

```json
{
  "status": "success",
  "artifact_ref": "runs/run_001/tasks/task_007/attempts/attempt_001/solution.py",
  "fitness": 0.87
}
```

`artifact_ref` may instead be passed as the tool argument; one of the two is
required.

Response:

```json
{
  "recorded": true,
  "attempt_id": "attempt_001",
  "in_success_pool": true,
  "success_record_id": "task_007",
  "fitness": 0.87,
  "relative_delta_to_parent": 0.034,
  "note_requests": []
}
```

`success_record_id` equals the task id and is `null` when the attempt did not
enter the pool. `relative_delta_to_parent` is
`(fitness - parent.fitness) / |parent.fitness|`, or `null` without a parent.

### `check_convergence`

Input:

```python
check_convergence(run_id: str, concurrency: int)
```

Output:

```json
{
  "action": "continue",
  "tasks_to_create": 2,
  "search_state": "exploring",
  "plateau": false,
  "stop_reason": "",
  "meta_guidance": {"enabled": true, "has_pending_request": false, "active_count": 0, "...": "..."},
  "diagnostics": {"...": "..."}
}
```

`request_id` is added when `action` is `request_meta_guidance`.
`search_state` is one of `exploring`, `watching`, `stopped`. `meta_guidance`
and `diagnostics` are diagnostic blocks that the orchestrator does not need to
read.

`action` is the primary semantics:

```text
continue              # keep scheduling; create tasks_to_create new tasks
request_meta_guidance # the plateau has lasted long enough; dispatch the dedicated worker to generate meta-guidance
budget_exhausted      # hard budget reached; stop submitting new tasks
```

This call is also the run's only task faucet: on `continue` it mints new task
ids into the task pool and reports the count as `tasks_to_create`.

Top-up target (when `action == "continue"`). The concurrency cap (the
`concurrency` argument: required, must be a positive integer, non-positive
values raise; pass the `max_concurrent_tasks` confirmed by `configure_run`)
holds in every phase, and the number of tasks to create follows three phases:

- **Root phase** (tasks created < `lineage_root_number`): top up in-flight
  tasks to the cap, but never exceed `lineage_root_number` tasks in total
  (exactly one root task per initial direction).
- **Root barrier** (tasks created == `lineage_root_number` with unfinished
  tasks): returns `action == "continue"` with `tasks_to_create == 0` until every
  root task has finished. This is a normal waiting state, not a stop signal.
- **Evolution phase** (all roots finished): top up to the cap after every
  completion.
- Every phase is additionally truncated by the `max_total_tasks` hard budget.

Example: with `max_concurrent_tasks=2`, `lineage_root_number=3`, the first call
creates 2; after one finishes, 1 more is created (roots reach 3); then nothing
until all 3 finish, after which 2 are created, and thereafter each completion
tops back up to 2. With `max_concurrent_tasks=3`, `lineage_root_number=2`, the
first call creates 2; after both finish, 3 are created, and thereafter each
completion tops back up to 3.

Constraints:

- Plateau / WATCHING never stops tasks.
- Once convergence detection enters WATCHING, the MCP returns
  `request_meta_guidance` directly.
- WATCHING does not change parent / inspiration selection parameters.
- With meta-guidance disabled, plateau diagnostics are still kept but no
  guidance request is created.

### `get_meta_guidance_request`

Input:

```python
get_meta_guidance_request(run_id: str, request_id: str = "")
```

Returns the pending request for the stateless `meta-guidance-generator` to
produce structured guidance. The orchestrator passes only `run_id` and
`request_id` and never reads this payload. The request carries:

- the original `task_description`
- `current_best`: the canonical best at request time, the anchor parent for
  the generated guidance (record id, fitness, artifact ref, summary, metrics,
  profile). `artifact_excerpt` and `artifact_excerpt_truncated` are read from
  the artifact when the request is fetched, not stored in state.
- `progress`: plateau / progress diagnostics
- `lineages`: per-lineage summaries
- `notes_summary`: run-local Notes distilled for guidance generation
- `guidance_history` and `failed_guidance`: what earlier guidance tried and
  how it fared
- `generation_instructions` and `constraints`

When there is no pending request, or `request_id` does not match it, returns
`available: false` with a reason.

### `commit_meta_guidance`

Input:

```python
commit_meta_guidance(run_id: str, request_id: str, guidance_json: Any)
```

`guidance_json` format:

```json
{
  "guidance": [
    {
      "idea": "One concise idea",
      "description": "How this changes the search direction",
      "target_metric": "fitness",
      "novelty_claim": "Why this is different from recent attempts",
      "implementation_plan": ["Step 1", "Step 2"],
      "cautions": ["Keep output contract unchanged"],
      "approach_type": "representation",
      "avoid_repeating": ["Known failed route"],
      "max_uses": 2
    }
  ]
}
```

The dedicated worker calls this tool itself; the MCP validates and activates
the guidance. The orchestrator confirms the commit through a compact
convergence re-check before topping up and never receives the guidance JSON.
Later `select_parent` calls assign active guidance to tasks, bind it to the
latest canonical best at that moment, and consume one use each.

### `finalize_run`

Input:

```python
finalize_run(run_id: str, stop_reason: str = "")
```

Response:

```json
{
  "report_ref": "runs/run_20260509_001/run_summary.md",
  "summary_ref": "runs/run_20260509_001/run_summary.json"
}
```

Marks the run `stopped` (keeping the stop reason already recorded by
`check_convergence` unless `stop_reason` is given) and writes both summaries.
Finalization is idempotent: calling it again regenerates the same files from
current state.

## 3. Local state files

All Python-side state lives under `.evotrias/db/runs/<run_id>/`, relative to
the working directory (there is no path environment variable):

```text
config.json                  every run setting, written once by configure_run and locked
run_state.json               task_description, search state, stop reason, diagnostics
task_contexts.json           per-task parent / inspirations / guidance assignment / outcome
attempts.jsonl               one record per recorded attempt; the success pool derives from it
notes.jsonl                  committed run-local notes
note_requests.jsonl          note requests issued to task-executors
notes_events.jsonl           complete Notes timeline (request created/skipped, validation failed, note committed)
meta_guidance_state.json     pending / active / tried guidance, watching marker, task assignments
meta_guidance_events.jsonl   full meta-guidance lifecycle (watching, request, commit, assignment, outcome, archive)
task_pool.json               scheduler task buckets (created / pending / queued / running / completed / failed); written by the scheduler
agent_index.json             scheduler agent registry; written by the scheduler
```

The success pool is not a file: it is rebuilt in memory on every call from
`attempts.jsonl` (records with `in_success_pool`) plus `task_contexts.json`
(selection counts).

The MCP owns everything under `.evotrias/`. Artifacts, verifier results and the
run workspace under `runs/<run_id>/` are owned by the caller/plugin and are only
referenced by path-like refs. The one exception is the finalize report, which
the MCP writes to `runs/<run_id>/run_summary.md` and
`runs/<run_id>/run_summary.json`.

Exhausted meta-guidance is retained in full for the lifetime of the run; it is
never truncated from the persisted history.

## 4. Anchor parent selection

When meta-guidance is activated, the canonical best at creation time is
recorded; every time guidance is assigned to a task, it is re-bound to the
canonical best at that moment:

1. highest fitness;
2. on ties, earliest `created_at`;
3. still tied, smallest `record_id` in lexical order.

This handles several implementations of the current best without drifting
between them, and once one guidance produces a new best, the remaining guidance
in the same batch continues from the latest best.

## 5. Prompt assembly boundary

The MCP returns only refs and ready-to-inject prompt sections; it never reads
or concatenates artifact content for the task-executor. The one exception is
the `artifact_excerpt` handed to the meta-guidance worker inside
`get_meta_guidance_request`.

Task-executor order:

1. `select_parent`
2. `select_inspirations`
3. `get_notes_prompt`
4. `get_meta_guidance_prompt`
5. read the local artifacts the refs point to
6. generate a candidate and run the verifier
7. `record_attempt`

How meta-guidance relates to parent / inspirations:

- the parent is the anchor, baseline, implementation reference or fallback;
- inspirations are ordinary references;
- meta-guidance may demand a clearly different new direction;
- the task-executor is not required to force-merge conflicting inspirations;
- no guidance can override the task requirements or the verifier output
  contract.

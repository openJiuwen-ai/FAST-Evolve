# MCP Interface Reference

The EvoTrias controller exposes its evolution control as an MCP server. This document lists the parameters and return values of every public tool, for reference when writing agent prompts and for downstream development.

| Item | Value |
| --- | --- |
| Server name | `evotrias` |
| Transport | stdio |
| Launch command | `python -m evotrias.mcp` (written into the coding agent's configuration by `evotrias setup`) |
| State directories | Agent workspace `runs/<run_id>/`; controller-internal state `.evotrias/` (both relative to the current working directory) |

## Tool Overview

MCP tools are called by three kinds of agent:

- **Main agent**: the agent in the user's session; after loading the `evolve` skill, it orchestrates the entire run.
- **`evotrias-task-executor`**: a subagent the main agent dispatches for each task; it generates and evaluates candidate programs.
- **`evotrias-meta-guidance-generator`**: a subagent the main agent dispatches when the search stalls; it generates Meta-guidance.

Every tool identifies a run by `run_id`; task-level tools additionally identify a task by `task_id`.

| Tool | Caller | Purpose |
| --- | --- | --- |
| [`configure_run`](#configure_run) | Main agent | Register run parameters and start the dashboard |
| [`check_convergence`](#check_convergence) | Main agent | Return the next scheduling action and create new tasks as needed |
| [`finalize_run`](#finalize_run) | Main agent | Generate the run summary |
| [`select_parent`](#select_parent) | `evotrias-task-executor` | Select the parent for a task |
| [`select_inspirations`](#select_inspirations) | `evotrias-task-executor` | Select inspiration candidates for a task |
| [`get_notes_prompt`](#get_notes_prompt) | `evotrias-task-executor` | Get the Notes applicable to the task |
| [`get_meta_guidance_prompt`](#get_meta_guidance_prompt) | `evotrias-task-executor` | Get the Meta-guidance assigned to the task |
| [`record_attempt`](#record_attempt) | `evotrias-task-executor` | Record the evaluation result of one attempt |
| [`commit_generated_note`](#commit_generated_note) | `evotrias-task-executor` | Commit one Note |
| [`get_meta_guidance_request`](#get_meta_guidance_request) | `evotrias-meta-guidance-generator` | Get the pending Meta-guidance request |
| [`commit_meta_guidance`](#commit_meta_guidance) | `evotrias-meta-guidance-generator` | Commit and activate Meta-guidance |

## Call Flow

```text
Main agent (evolve skill)
  configure_run ─► check_convergence (loop) ─► finalize_run
                     │
                     ├─ action = continue               ─► dispatch tasks_to_create evotrias-task-executor
                     ├─ action = request_meta_guidance  ─► dispatch evotrias-meta-guidance-generator with request_id
                     └─ action = budget_exhausted       ─► wait for in-flight tasks, then finalize

evotrias-task-executor (one per task)
  select_parent ─► select_inspirations ─► get_notes_prompt ─► get_meta_guidance_prompt
    ─► generate a candidate and run the evaluator ─► record_attempt ─► commit_generated_note (one per note_requests entry)

evotrias-meta-guidance-generator
  get_meta_guidance_request ─► commit_meta_guidance
```

## configure_run

Registers the parameters of a run. Must be called before any other tool for the same `run_id`. The first call writes the configuration, which is then locked; repeated calls return the locked values.

**Parameters**

| Parameter | Type | Required | Default | Description |
| --- | --- | :---: | --- | --- |
| `run_id` | string | ✓ | — | Run identifier |
| `max_total_tasks` | integer | | `50` | Upper limit on the total number of tasks |
| `lineage_root_number` | integer | | `4` | Number of initial exploration directions, i.e. lineage root tasks; minimum 1 |
| `max_concurrent_tasks` | integer | | `4` | Maximum number of parallel tasks; minimum 1 |
| `task_description` | string | | `""` | The original task description submitted by the user |

**Returns**

| Field | Type | Description |
| --- | --- | --- |
| `configured` | boolean | Whether this call wrote the configuration; `false` if a configuration already exists |
| `message` | string | Result message |
| `max_total_tasks` | integer | Effective task limit |
| `lineage_root_number` | integer | Effective number of initial directions |
| `max_concurrent_tasks` | integer | Effective maximum number of parallel tasks |
| `meta_evolve` | object | Meta-guidance configuration: `enabled`, `guidance_num_to_generate`, `guidance_max_uses` |
| `dashboard` | object | Dashboard status: `enabled`, `started`; includes `url` and `port` on success, `error` on failure, `reason` when disabled |

## check_convergence

Returns the next scheduling action based on the current search state. When it returns `continue`, it also creates new tasks in the task pool; it is the only task-creation entry point in a run.

In the root phase, the total number of tasks does not exceed `lineage_root_number`. No evolution task is created until every root task has finished; meanwhile the tool returns `continue` with `tasks_to_create` set to `0`, which is a normal waiting state. In every phase, in-flight tasks never exceed `concurrency`, and the total never exceeds `max_total_tasks`.

**Parameters**

| Parameter | Type | Required | Default | Description |
| --- | --- | :---: | --- | --- |
| `run_id` | string | ✓ | — | Run identifier |
| `concurrency` | integer | ✓ | — | Limit on in-flight tasks; must be a positive integer, normally `max_concurrent_tasks` |

**Returns**

| Field | Type | Description |
| --- | --- | --- |
| `action` | string | `continue`: keep scheduling; `request_meta_guidance`: Meta-guidance is needed; `budget_exhausted`: budget exhausted, stop creating tasks |
| `tasks_to_create` | integer | Number of tasks created by this call; can be greater than 0 only for `continue` |
| `request_id` | string | Meta-guidance request identifier; returned only for `request_meta_guidance` |
| `search_state` | string | Search phase: `exploring`, `watching` (observing a plateau), or `stopped` |
| `plateau` | boolean | Whether the search is in the plateau-watching state |
| `stop_reason` | string | Stop reason; empty if not stopped |
| `meta_guidance` | object | Meta-guidance statistics: `enabled`, `has_pending_request`, `active_count`, `active_with_remaining`, `tried_count`, `watching` |
| `diagnostics` | object | Plateau detection diagnostics, such as `current_best_fitness`, `plateau_count`, `window`, and `patience` |

## finalize_run

Generates the run summary from persisted run state. It can be called repeatedly; each call regenerates the summary from the current state without modifying candidates or attempt history.

**Parameters**

| Parameter | Type | Required | Default | Description |
| --- | --- | :---: | --- | --- |
| `run_id` | string | ✓ | — | Run identifier |
| `stop_reason` | string | | `""` | External stop reason (e.g. user interruption); leave empty on normal convergence or budget exhaustion to keep the recorded reason |

**Returns**

| Field | Type | Description |
| --- | --- | --- |
| `report_ref` | string | Markdown summary path: `runs/<run_id>/run_summary.md` |
| `summary_ref` | string | JSON summary path: `runs/<run_id>/run_summary.json` |

## select_parent

Selects the parent for a task and caches it in the task context, so later tools do not need it passed again. Repeated calls for the same task return the same result. Root tasks have no parent; when a task is assigned Meta-guidance, its parent is fixed to the current best candidate.

**Parameters**

| Parameter | Type | Required | Default | Description |
| --- | --- | :---: | --- | --- |
| `run_id` | string | ✓ | — | Run identifier |
| `task_id` | string | ✓ | — | Task identifier |

**Returns**

| Field | Type | Description |
| --- | --- | --- |
| `parent_id` | string \| null | Parent candidate identifier; `null` for root tasks |
| `artifact_ref` | string \| null | Parent artifact path |
| `fitness` | number \| null | Parent fitness |
| `metrics` | object | Parent evaluation metrics; `{}` for root tasks |

## select_inspirations

Selects inspiration candidates for a task; the result excludes the parent. Must be called after `select_parent`; repeated calls for the same task return the same result.

**Parameters**

| Parameter | Type | Required | Default | Description |
| --- | --- | :---: | --- | --- |
| `run_id` | string | ✓ | — | Run identifier |
| `task_id` | string | ✓ | — | Task identifier |

**Returns**

| Field | Type | Description |
| --- | --- | --- |
| `inspirations` | array | Inspiration candidates, each with `inspiration_id`, `artifact_ref`, `fitness`, and `metrics`; empty when there is no parent |

## get_notes_prompt

Returns the Notes relevant to the task's parent and inspiration candidates, rendered as a ready-to-inject prompt section. Must be called after `select_parent` and `select_inspirations`.

**Parameters**

| Parameter | Type | Required | Default | Description |
| --- | --- | :---: | --- | --- |
| `run_id` | string | ✓ | — | Run identifier |
| `task_id` | string | ✓ | — | Task identifier |

**Returns**

| Field | Type | Description |
| --- | --- | --- |
| `available` | boolean | Whether any Notes are available |
| `prompt` | string | Prompt section; empty when no Notes are available |
| `selected_note_ids` | array | Identifiers of the selected Notes |
| `counts` | object | Note counts by kind: `error`, `positive`, `negative`, `matched_pos_neg`, `random_fill` |
| `disabled` | boolean | Returned only when Notes are disabled, with value `true` |
| `reason` | string | Returned only when Notes are disabled |

## get_meta_guidance_prompt

Returns the Meta-guidance assigned to the task, rendered as a ready-to-inject prompt section. Must be called after `select_parent`.

**Parameters**

| Parameter | Type | Required | Default | Description |
| --- | --- | :---: | --- | --- |
| `run_id` | string | ✓ | — | Run identifier |
| `task_id` | string | ✓ | — | Task identifier |

**Returns**

| Field | Type | Description |
| --- | --- | --- |
| `available` | boolean | Whether the task has been assigned Meta-guidance |
| `reason` | string | Reason when unavailable: `no_active_guidance` or `assigned_guidance_not_found` |
| `guidance_id` | string | Meta-guidance identifier |
| `anchor_parent_id` | string | Anchor parent of this task |
| `anchor_fitness` | number \| null | Fitness of the anchor parent |
| `creation_anchor_parent_id` | string | Anchor parent at the time the Meta-guidance was generated |
| `prompt` | string | Prompt section |

When `available` is `false`, only `available` and `reason` are returned.

## record_attempt

Records the evaluation result of one attempt. Successful attempts with a numeric fitness enter the success pool; the parent and inspiration candidates are linked automatically from the task context. Repeated calls with the same `task_id` and `attempt_id` are idempotent.

**Parameters**

| Parameter | Type | Required | Default | Description |
| --- | --- | :---: | --- | --- |
| `run_id` | string | ✓ | — | Run identifier |
| `task_id` | string | ✓ | — | Task identifier |
| `verification_result_ref` | string | ✓ | — | Path to the evaluation result file; see [Evaluation Result File](#evaluation-result-file) |
| `attempt_id` | string | | `""` | Attempt identifier; when empty, inferred from the result file, then from the name of its directory (e.g. `attempt_001`) |
| `artifact_ref` | string | | `""` | Candidate artifact path; when empty, read from `artifact_ref` in the result file; an error is raised if both are missing |
| `max_attempts` | integer | | `3` | Maximum attempts for this task, used to determine whether this is the final attempt |

**Returns**

| Field | Type | Description |
| --- | --- | --- |
| `recorded` | boolean | Always `true` |
| `attempt_id` | string | Effective attempt identifier |
| `in_success_pool` | boolean | Whether the attempt entered the success pool |
| `success_record_id` | string \| null | Success pool record identifier |
| `fitness` | number \| null | Fitness of this attempt |
| `relative_delta_to_parent` | number \| null | Relative fitness change against the parent |
| `note_requests` | array | Pending Note requests, each with `note_request_id`, `note_type` (`error`, `positive`, or `negative`), `analysis_prompt`, and `expected_schema`; each must be committed via `commit_generated_note` |

## commit_generated_note

Validates and commits one Note in response to a Note request returned by `record_attempt`.

**Parameters**

| Parameter | Type | Required | Default | Description |
| --- | --- | :---: | --- | --- |
| `run_id` | string | ✓ | — | Run identifier |
| `note_request_id` | string | ✓ | — | Note request identifier |
| `generated_note_json` | object \| string | ✓ | — | Note content, as an object or a JSON string |

`generated_note_json` has the following fields. Content must not include code blocks, long logs, or reasoning traces.

| Field | Required | Constraint |
| --- | :---: | --- |
| `title` | ✓ | At most 120 characters |
| `lesson` | ✓ | At most 400 characters |
| `actionable_guidance` | ✓ | At most 400 characters |
| `evidence_summary` | ✓ | At most 500 characters |
| `confidence` | | `low`, `medium`, or `high`; defaults to `medium` |

**Returns**

| Field | Type | Description |
| --- | --- | --- |
| `committed` | boolean | Whether the commit succeeded |
| `note_id` | string | Note identifier; returned on success |
| `already_committed` | boolean | `true` when the Note had already been committed |
| `retryable` | boolean | Returned on failure; whether a corrected Note can be resubmitted |
| `reason` | string | Failure reason |
| `disabled` | boolean | Returned only when Notes are disabled, with value `true` |

## get_meta_guidance_request

Gets the current pending Meta-guidance request, which serves as the basis for generating Meta-guidance.

**Parameters**

| Parameter | Type | Required | Default | Description |
| --- | --- | :---: | --- | --- |
| `run_id` | string | ✓ | — | Run identifier |
| `request_id` | string | | `""` | Request identifier; when empty, the current pending request is returned |

**Returns**

| Field | Type | Description |
| --- | --- | --- |
| `available` | boolean | Whether a matching pending request exists |
| `reason` | string | Reason when unavailable: `no_pending_meta_guidance_request` or `meta_guidance_request_not_found` |
| `request_id` | string | Request identifier |
| `task_description` | string | Original task description |
| `current_best` | object | Current best candidate: `record_id`, `fitness`, `artifact_ref`, `summary`, `metrics`, `artifact_excerpt` (excerpt of the artifact content), and more |
| `progress` | object | Plateau progress: `completed_since_watching`, `best_at_watching_start`, `current_best_fitness`, `diagnostics` |
| `lineages` | array | Per-lineage statistics: `root_id`, `size`, `best_fitness`, `share`, `recent_improvement`, and more |
| `notes_summary` | object | Summary of existing Notes |
| `guidance_history` | array | Previously tried Meta-guidance and its outcomes |
| `failed_guidance` | array | Meta-guidance that brought no improvement |
| `generation_instructions` | array | Generation requirements |
| `constraints` | array | Constraints that must be respected |

When `available` is `false`, only `available` and `reason` are returned.

## commit_meta_guidance

Validates and activates a batch of Meta-guidance. The batch is written only after every item passes validation; if any item is rejected, the pending request is left unchanged and can be retried after correction.

**Parameters**

| Parameter | Type | Required | Default | Description |
| --- | --- | :---: | --- | --- |
| `run_id` | string | ✓ | — | Run identifier |
| `request_id` | string | ✓ | — | Request identifier; must match the current pending request |
| `guidance_json` | object \| array \| string | ✓ | — | Meta-guidance list, as `{"guidance": [...]}`, an array, or the equivalent JSON string |

Each Meta-guidance item has the following fields. The number of items must not exceed `guidance_num_to_generate`, and `approach_type` must be unique across items.

| Field | Required | Description |
| --- | :---: | --- |
| `idea` | ✓ | Core idea |
| `description` | ✓ | Detailed description |
| `novelty_claim` | ✓ | How it differs from directions already tried |
| `implementation_plan` | ✓ | List of implementation steps |
| `approach_type` | ✓ | Approach category |
| `target_metric` | | Target metric; defaults to `fitness` |
| `current_best_assessment` | | Assessment of the current best solution |
| `relationship_to_current_best` | | Relationship to the current best solution; defaults to `critically_adapt` |
| `cautions` | | List of cautions |
| `avoid_repeating` | | List of practices not to repeat |
| `max_uses` | | Number of tasks it may be assigned to, clamped to 1 through `guidance_max_uses` |

**Returns**

| Field | Type | Description |
| --- | --- | --- |
| `committed` | boolean | Whether the commit succeeded |
| `request_id` | string | Request identifier; returned on success |
| `guidance_ids` | array | Identifiers of the newly activated Meta-guidance; returned on success |
| `anchor_parent_id` | string | Anchor parent; returned on success |
| `reason` | string | Failure reason, such as `pending_request_not_found`, `empty_guidance`, `too_many_guidance_items`, `duplicate_approach_type`, `missing_required_field:<field>`, `unsafe_guidance`, or `meta_guidance_disabled` |
| `max` | integer | Maximum number of items allowed; returned only for `too_many_guidance_items` |

## Evaluation Result File

The evaluation result read by `record_attempt` is a JSON object, at either an absolute path or a path relative to the current working directory. The controller uses the following fields:

| Field | Required | Description |
| --- | :---: | --- |
| `status` | ✓ | `success` means the candidate is valid; any other value is treated as a failure |
| `fitness` | ✓ | Top-level numeric fitness, higher is better; a missing or non-numeric value keeps the attempt out of the success pool |
| `metrics` | | Evaluation metrics object |
| `artifact_ref` | | Candidate artifact path; required when not passed as a parameter |
| `attempt_id` | | Attempt identifier |
| `summary` | | Brief description of the candidate |

```json
{
  "status": "success",
  "artifact_ref": "runs/demo/tasks/task_001/attempts/attempt_001/solution.py",
  "fitness": 2.6158,
  "metrics": { "validity": 1.0, "sum_radii": 2.6158, "target_ratio": 0.9927 }
}
```

## Environment Variables

The following environment variables must be set before the MCP server starts. The first three are resolved at `configure_run` and written into the run's configuration, after which they no longer change.

| Variable | Default | Description |
| --- | --- | --- |
| `EVOTRIAS_META_EVOLVE_ENABLED` | Enabled | Set to `0` to disable Meta-guidance; plateau detection and regular selection are unaffected |
| `EVOTRIAS_NOTES_ENABLED` | Enabled | Set to `0` to disable Notes |
| `EVOTRIAS_PERF_IMPROVEMENT_THRESHOLD` | `0.0` | Relative improvement threshold for the best fitness; improvements below it count as a plateau |
| `EVOTRIAS_UI_ENABLED` | Enabled | Set to `0` to disable automatic dashboard startup |
| `EVOTRIAS_UI_HOST` | `127.0.0.1` | Bind address of the dashboard server; by default only local access is allowed |
| `EVOTRIAS_UI_PUBLIC_HOST` | Same as `EVOTRIAS_UI_HOST` | Host name used in the dashboard URL (the `dashboard.url` returned by `configure_run`); does not affect the bind address |

For remote access to the dashboard, set both `EVOTRIAS_UI_HOST=0.0.0.0` and `EVOTRIAS_UI_PUBLIC_HOST=<host name or IP>`; otherwise the printed URL uses `0.0.0.0` as its host and cannot be opened directly. Neither is needed for local access.

## Error Conventions

- **Call errors**: When parameters are invalid or a precondition is not met (e.g. calling `select_inspirations` before `select_parent`, a non-positive `concurrency`, or an unreadable result file), the tool raises an exception that is returned through the standard MCP error channel.
- **Business rejections**: When submitted content fails validation, commit tools return normally with `committed: false` and a `reason`, and run state is left unchanged.

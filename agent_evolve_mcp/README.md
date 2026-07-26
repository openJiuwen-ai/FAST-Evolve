# Agent Evolve MCP

`agent_evolve_mcp` is the Evolution Controller MCP module for Agent Evolve. It exposes a small FastMCP interface for parent selection, inspiration selection, attempt recording, convergence/meta-guidance checking, and deterministic run finalization.

The implementation follows a minimal-context contract: agents pass only stable ids and refs, while the MCP keeps detailed state in local files.

## Tools

```python
evolve_configure_run(run_id: str, max_total_tasks: int = 50, task_description: str = "")
evolve_update_task_status(run_id: str, task_id: str, status: str, agent_id: str = "")
evolve_get_scheduler_state(run_id: str)
evolve_select_parent(run_id: str, task_id: str)
evolve_select_inspirations(run_id: str, task_id: str)
evolve_record_attempt(run_id: str, task_id: str, verification_result_ref: str)
evolve_get_meta_guidance_request(run_id: str, request_id: str = "")
evolve_commit_meta_guidance(run_id: str, request_id: str, guidance_json: Any)
evolve_get_meta_guidance_prompt(run_id: str, task_id: str)
evolve_check_convergence(run_id: str)
evolve_finalize_run(run_id: str, stop_reason: str = "")
```

Public responses are intentionally compact:

- parent/inspiration calls return ids, `artifact_ref`, compact `fitness`, and evaluator `metrics` for diagnostic comparison
- scheduler calls persist task transitions and return only active state or a compact acknowledgement, avoiding full `scheduler_state.json` diffs in the host terminal
- attempt recording returns `entered_success_db` and `success_record_id`
- convergence returns `action`: `continue`, `request_meta_guidance`, or `budget_exhausted`
- meta-guidance request/commit/prompt tools keep guidance state in MCP instead of task-executor memory
- a stateless `meta-guidance-generator` receives only `run_id` and `request_id`,
  fetches the pending request itself, commits its own JSON, and returns only a
  compact result to the host orchestrator
- finalization writes `runs/<run_id>/run_summary.md` and `runs/<run_id>/run_summary.json`, then returns only refs

## State

By default, controller state is stored under:

```text
.agent_evolve/controller/{run_id}/
```

Override it with:

```text
AGENT_EVOLVE_CONTROLLER_DIR
```

Plateau detection is always active and no longer causes early stop. WATCHING
immediately returns `action="request_meta_guidance"` when meta-guidance is
enabled. It does not alter parent or inspiration selection. Hard task/time
budgets remain the only automatic stop conditions.

By default, the best-fitness improvement threshold is `0.0`: any positive
running-best improvement is progress, and equal running-best fitness is
counted as a plateau. For noisy verifier domains such as operator profiling,
set a relative fuzzy boundary before starting the MCP process:

```text
AGENT_EVOLVE_PERF_IMPROVEMENT_THRESHOLD=0.01
```

Meta-evolve defaults:

```yaml
meta_evolve:
  enabled: true
  guidance_num_to_generate: 3
  guidance_max_uses: 2
  max_tried_guidance: 10
```

`AGENT_EVOLVE_META_EVOLVE_ENABLED=0` disables meta-guidance request creation.

Active meta-guidance is generated from the canonical best at request time, but
each task binds it to the latest canonical best when `evolve_select_parent` is
called. This mirrors AdaEvolve-style paradigm use: the idea persists, while the
parent baseline follows new bests.

The MCP owns `success_db.json`, `attempts.jsonl`, `task_contexts.json`, `run_state.json`, `notes.json`, and the `meta_guidance/` state files. Artifact files, verifier logs, and run workspaces are owned by the caller/plugin and are referenced by path-like refs.

Run summaries are generated as experiment artifacts under:

```text
runs/{run_id}/run_summary.md
runs/{run_id}/run_summary.json
```

## Docs

- [Minimal context interface](docs/minimal_context_interface.md)
- [Implementation guide](docs/implementation_guide.md)

## Checks

```powershell
python -m compileall agent_evolve_mcp
```

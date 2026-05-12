# Agent Evolve MCP

`agent_evolve_mcp` is the Evolution Controller MCP module for Agent Evolve. It exposes a small FastMCP interface for parent selection, inspiration selection, attempt recording, convergence checking, and deterministic run finalization.

The implementation follows a minimal-context contract: agents pass only stable ids and refs, while the MCP keeps detailed state in local files.

## Tools

```python
evolve_select_parent(run_id: str, task_id: str)
evolve_select_inspirations(run_id: str, task_id: str)
evolve_record_attempt(run_id: str, task_id: str, verification_result_ref: str)
evolve_check_convergence(run_id: str)
evolve_finalize_run(run_id: str, stop_reason: str = "")
```

Public responses are intentionally compact:

- parent/inspiration calls return ids, `artifact_ref`, and compact `fitness`
- attempt recording returns `entered_success_db` and `success_record_id`
- convergence returns only `should_stop`
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

Convergence checking is enabled by default. To disable soft convergence early
stops, restart the MCP process with:

```text
AGENT_EVOLVE_CONVERGENCE_ENABLED=false
```

This switch does not disable hard budgets. `max_total_tasks` and
`max_time_seconds` can still make `evolve_check_convergence` return
`{"should_stop": true}`.

The MCP owns `success_db.json`, `attempts.jsonl`, `task_contexts.json`, and `run_state.json`. Artifact files, verifier logs, and run workspaces are owned by the caller/plugin and are referenced by path-like refs.

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

# Agent Evolve MCP

`agent_evolve_mcp` is the Evolution Controller MCP module for Agent Evolve. It exposes a small FastMCP interface for parent selection, inspiration selection, attempt recording, and convergence checking.

The implementation follows a minimal-context contract: agents pass only stable ids and refs, while the MCP keeps detailed state in local files.

## Tools

```python
evolve_select_parent(run_id: str, task_id: str)
evolve_select_inspirations(run_id: str, task_id: str)
evolve_record_attempt(run_id: str, task_id: str, verification_result_ref: str)
evolve_check_convergence(run_id: str)
```

Public responses are intentionally compact:

- parent/inspiration calls return ids and `artifact_ref`
- attempt recording returns `entered_success_db` and `success_record_id`
- convergence returns only `should_stop`

## State

By default, controller state is stored under:

```text
.agent_evolve/controller/{run_id}/
```

Override it with:

```text
AGENT_EVOLVE_CONTROLLER_DIR
```

The MCP owns `success_db.json`, `attempts.jsonl`, `task_contexts.json`, and `run_state.json`. Artifact files, verifier logs, and run workspaces are owned by the caller/plugin and are referenced by path-like refs.

## Docs

- [Minimal context interface](docs/minimal_context_interface.md)
- [Implementation guide](docs/implementation_guide.md)

## Checks

```powershell
python -m compileall agent_evolve_mcp
```

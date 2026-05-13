# Agent Evolve

Agent Evolve is a self-evolution experiment scaffold. It currently contains:

- `agent_evolve_mcp/`: Evolution Controller MCP. It selects parents and inspirations, records verifier results, checks convergence, and writes deterministic run summaries.
- `agent_evolve_plugin/`: Claude Code workflow plugin. It provides the main `evolve` skill and the `task-executor` subagent workflow.
- `benchmarks/`: benchmark skills and evaluators. Currently there are 14 math problems including `circle_packing` added to benchmarks.

Runtime state is local and ignored by git:

```text
.agent_evolve/
runs/
```

## Install

```powershell
pip install -r requirements.txt
uv sync --extra math
```

## Runtime Switches

Convergence checking is enabled by default. To disable soft convergence early
stops, restart Claude Code/MCP with:

```powershell
$env:AGENT_EVOLVE_CONVERGENCE_ENABLED = "false"
```

When this switch is false, `evolve_check_convergence` skips plateau-based
convergence stops. Hard budgets still apply: `max_total_tasks` and
`max_time_seconds` can still return `should_stop=true`.

## Run Circle Packing E2E

Start Claude Code with both plugins:

```powershell
claude --plugin-dir agent_evolve_plugin --plugin-dir benchmarks
```

Then ask Claude Code:

```text
Use the evolve skill to run the circle_packing benchmark end to end.
```

The main skill should generate a timestamp run id such as:

```text
run_20260511143310
```

When MCP decides the run should stop, the workflow stops launching replacement
tasks, waits for already launched tasks to finish, then calls
`evolve_finalize_run`. The final report is written to:

```text
runs/<run_id>/run_summary.md
runs/<run_id>/run_summary.json
```

## Local Checks

```powershell
python -m compileall agent_evolve_mcp agent_evolve_plugin benchmarks\skills\circle_packing\scripts
```

You can also smoke-test finalization from Python when a controller state exists:

```powershell
python -c "from agent_evolve_mcp.public_tools import evolve_finalize_run; print(evolve_finalize_run('run_20260509_001'))"
```

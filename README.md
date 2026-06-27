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
uv run python benchmarks/install.py --all --clear
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

By default, convergence checking treats any positive best-fitness improvement
as progress. Equal running-best fitness is not counted as an S1 plateau. For
noisy verifier domains such as operator profiling, restart Claude Code/MCP with
a relative fuzzy boundary:

```powershell
$env:AGENT_EVOLVE_PERF_IMPROVEMENT_THRESHOLD = "0.01"
```

With this setting, best-fitness improvement below 1% over the convergence
window is treated as plateau.

## Run Circle Packing E2E

Start Claude Code with both plugins:

```powershell
claude --plugin-dir agent_evolve_plugin --plugin-dir benchmarks
```

Then ask Claude Code:

```text
/agent_evolve_plugin:evolve run the circle_packing benchmark end to end.
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

## Run Artifacts

Each run leaves two main groups of local files. They are runtime artifacts and
should normally stay out of git.

`runs/<run_id>/` is the run workspace. It contains task artifacts, verifier
outputs, and final summaries:

```text
runs/<run_id>/run_summary.md      # human-readable final report, if finalized
runs/<run_id>/run_summary.json    # structured final summary, if finalized
runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/
```

Each attempt directory usually contains the generated artifact and verifier
result:

```text
solution.py        # or the benchmark-specific artifact
eval_result.json   # verifier output used by MCP recording
```

`.agent_evolve/controller/<run_id>/` is the Evolution Controller MCP state. The
important files are:

```text
success_db.json      # successful records reusable as parents/inspirations
attempts.jsonl       # append-only audit log of recorded attempts
task_contexts.json   # cached parent/inspiration choices for each task
run_state.json       # convergence state, stop reason, and diagnostics
```

For sharing results, prefer `run_summary.md`, `run_summary.json`, or a curated
benchmark report instead of committing the full `runs/` or `.agent_evolve/`
directories.

## Local Checks

```powershell
python -m compileall agent_evolve_mcp agent_evolve_plugin benchmarks\skills\circle_packing\scripts
```

You can also smoke-test finalization from Python when a controller state exists:

```powershell
python -c "from agent_evolve_mcp.public_tools import evolve_finalize_run; print(evolve_finalize_run('run_20260509_001'))"
```

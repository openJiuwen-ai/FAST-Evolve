# Agent Evolve

Agent Evolve is a self-evolution experiment scaffold. It currently contains:

- `agent_evolve_mcp/`: Evolution Controller MCP. It selects parents and inspirations, records verifier results, checks convergence, and writes deterministic run summaries.
- `agent_evolve_plugin/`: Claude Code workflow plugin. It provides the main `evolve` skill and the `task-executor` subagent workflow.
- `benchmarks/`: benchmark skills and evaluators. The current end-to-end benchmark is `circle_packing`.

Runtime state is local and ignored by git:

```text
.agent_evolve/
runs/
```

## Install

```powershell
pip install -r requirements.txt
```

## Claude Code Permissions

Claude Code background agents need explicit local permissions for writing
artifacts, running the benchmark evaluator, and calling MCP tools. Add or merge
the following entries into `.claude/settings.local.json`:

```json
{
  "permissions": {
    "allow": [
      "Skill(agent_evolve_plugin:evolve)",
      "Skill(benchmarks:circle_packing)",
      "Agent(task-executor)",
      "Write(/runs/**)",
      "Edit(/runs/**)",
      "Bash(mkdir *)",
      "Bash(python benchmarks/skills/circle_packing/scripts/evaluator.py *)",
      "mcp__plugin_agent_evolve_plugin_agent_evolve_mcp__evolve_select_parent",
      "mcp__plugin_agent_evolve_plugin_agent_evolve_mcp__evolve_select_inspirations",
      "mcp__plugin_agent_evolve_plugin_agent_evolve_mcp__evolve_record_attempt",
      "mcp__plugin_agent_evolve_plugin_agent_evolve_mcp__evolve_check_convergence",
      "mcp__plugin_agent_evolve_plugin_agent_evolve_mcp__evolve_finalize_run"
    ]
  }
}
```

`settings.local.json` is intentionally local-only and should not be committed.

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

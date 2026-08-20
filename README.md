<div align="center">

<img src="assets/evotrias-banner.png" alt="EvoTrias" width="500">

**English** | [中文](docs/README_CN.md)

</div>

EvoTrias coordinates coding agents, benchmark evaluators, and persistent search
state in one evolution loop. Give it an optimization task and a verifier; it
explores multiple directions, evaluates candidates, preserves useful discoveries,
and keeps searching from the strongest results.

## Highlights

- **Parallel exploration** — starts from distinct root directions and maintains a
  rolling queue of candidate-generation tasks.
- **Verifier-driven search** — benchmark-owned evaluators decide candidate fitness,
  keeping task-specific scoring separate from the controller.
- **Persistent search intelligence** — records successful candidates, lineage,
  attempts, and run-local Notes for later tasks.
- **Strategy adaptation** — detects plateaus and can generate bounded meta-guidance
  to refine or replace the current search strategy.
- **Live observability** — includes a read-only dashboard for fitness, lineage,
  active tasks, recent activity, Notes, and meta-guidance.
- **Extensible benchmarks** — ships with math, systems, and algorithmic benchmark
  skills, while keeping evaluator contracts owned by each benchmark.

## How It Works

```text
Host coding agent
  └─ initializes the run, schedules tasks, and delegates meta requests by ID
       ↓
Evolution Controller MCP
  ├─ manages selection, lineage, Notes, convergence, and meta-guidance state
  └─ exposes pending requests to a stateless meta-guidance-generator agent
       ↓
Task-executor agents
  └─ generate candidates and invoke the benchmark verifier
       ↓
Benchmark evaluators
  └─ return normalized fitness and artifacts to the controller
```

The controller keeps detailed state on disk and exchanges compact task contexts
with agents. This lets long-running searches reuse prior discoveries without
filling the host agent's context window.

## Quick Start

### Prerequisites

- Python 3.10–3.13
- [uv](https://docs.astral.sh/uv/)
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) for the bundled
  plugin workflow

### Install

From the repository root:

```powershell
python -m pip install -r requirements.txt
uv sync --extra adrs --extra math --extra dev
uv run python benchmarks/install.py --all
```

The benchmark installer registers the bundled benchmark skills and their
evaluator commands in `.claude/settings.local.json`.

### Run a Benchmark

Start Claude Code with the EvoTrias workflow and benchmark plugins:

```powershell
claude --plugin-dir agent_evolve_plugin --plugin-dir benchmarks
```

Then submit an evolution task:

```text
/agent_evolve_plugin:evolve run the circle_packing benchmark end to end.
```

EvoTrias creates a named run under `runs/`, launches the dashboard, explores
several initial directions, and continues scheduling candidates until the
configured task or time budget is exhausted.

## Repository Layout

```text
agent_evolve_mcp/       Evolution controller and persistence layer
agent_evolve_plugin/    Workflow skills and task-executor agents
benchmarks/             Benchmark skills, evaluators, and references
docs/                   Integration guides
tests/                  Controller and benchmark regression tests
```

Runtime artifacts are written to the gitignored `runs/` and `.agent_evolve/`
directories. Each completed run includes machine-readable results and a concise
Markdown summary.

## Defaults and Runtime Switches

| Setting | Default | Effect |
| --- | ---: | --- |
| Initial directions | 4 | Number of root exploration branches |
| Maximum concurrent tasks | 4 | Rolling scheduler queue limit |
| Default task budget | 50 | Used when the request does not specify a task count |
| Plateau window | 5 successes | Window used for plateau diagnostics |
| Plateau patience | 4 | Consecutive plateau count required before strategy adaptation |
| Fitness improvement threshold | 0.0 | A positive running-best improvement is progress |
| Meta-guidance items per request | 3 | Maximum distinct guidance items generated |
| Uses per guidance item | 2 | Maximum tasks assigned to each item |
| Retained tried guidance | 10 | Outcome history available to later guidance requests |

Configuration is locked when a run is initialized. Set these environment
variables before starting the MCP process:

```powershell
# Disable meta-guidance while retaining plateau diagnostics and normal selection.
$env:AGENT_EVOLVE_META_EVOLVE_ENABLED = "0"

# Disable run-local Notes.
$env:AGENT_EVOLVE_NOTES_ENABLED = "0"

# Disable automatic dashboard startup.
$env:AGENT_EVOLVE_UI_ENABLED = "0"

# Treat relative best-fitness improvement below 1% as plateau.
$env:AGENT_EVOLVE_PERF_IMPROVEMENT_THRESHOLD = "0.01"

# Store controller state outside the default project-local directory.
$env:AGENT_EVOLVE_CONTROLLER_DIR = "D:\evotrias-state"
```

Hard task and time budgets remain active independently of plateau detection.

## Run Artifacts

EvoTrias separates user-facing run outputs from controller-owned state:

```text
runs/<run_id>/
  completed_tasks.jsonl
  dashboard.json
  tasks/<task_id>/attempts/<attempt_id>/solution.py  # or solution.cpp
  tasks/<task_id>/attempts/<attempt_id>/eval_result.json
  run_summary.md
  run_summary.json
```

```text
.agent_evolve/controller/<run_id>/
  success_db.json
  attempts.jsonl
  task_contexts.json
  run_state.json
  notes.json
  meta_guidance/meta_guidance_state.json
  meta_guidance/meta_guidance_uses.jsonl
```

Both locations are local runtime state and are ignored by Git. Share the final
summaries or a curated benchmark report instead of committing complete run
directories.

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall agent_evolve_mcp agent_evolve_plugin benchmarks
```

For integration with another agent host, see the
[JiuwenClaw integration guide](docs/integration-jiuwenclaw.md).

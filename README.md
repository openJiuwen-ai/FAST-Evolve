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

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) for the bundled
  plugin workflow

### Install

From the repository root:

```powershell
uv sync
uv run python benchmarks/install.py --all
```

The benchmark installer registers the bundled benchmark skills and their
evaluator commands in `.claude/settings.local.json`.

### Benchmark Dependencies

Benchmarks are plugins; their Python dependencies are not part of the package.
Before running a benchmark, install the requirement set for its group:

```powershell
uv pip install -r benchmarks/requirements/math.txt   # math benchmarks
uv pip install -r benchmarks/requirements/adrs.txt   # ADRS benchmarks
uv pip install -r benchmarks/requirements/gpu.txt    # GPU kernel benchmarks
```

`frontier_cs_algo` needs no extra dependencies. Note that `uv sync` prunes
packages that are not declared by the project, so re-run the matching
`uv pip install` command after syncing (automatic installation via
`benchmarks/install.py` is planned).

Add the repository root to `PYTHONPATH` in the same shell session:

```powershell
# PowerShell
$env:PYTHONPATH = "$(Get-Location);$env:PYTHONPATH"
```

```bash
# Linux (bash)
export PYTHONPATH=`pwd`:$PYTHONPATH
```

### Run a Benchmark

Activate the project virtual environment:

```powershell
# PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# Linux (bash)
source .venv/bin/activate
```

Then start Claude Code with the EvoTrias workflow and benchmark plugins:

```powershell
claude --plugin-dir evotrias_plugin --plugin-dir benchmarks
```

Then submit an evolution task:

```text
/evotrias_plugin:evolve run the circle_packing benchmark end to end.
```

EvoTrias creates a named run under `runs/`, launches the dashboard, explores
several initial directions, and continues scheduling candidates until the
configured task or time budget is exhausted.

## Repository Layout

```text
evotrias/               Core package: db (state), evolve_ctrl (evolution control), dashboard, mcp (server entry), hooks, scheduler
evotrias_plugin/        Workflow skills and task-executor agents
benchmarks/             Benchmark skills, evaluators, and references
docs/                   Integration guides
tests/                  Testcases
```

The controller MCP tool contract is documented in [docs/mcp_interface.md](docs/mcp_interface.md).

Runtime artifacts are written to the gitignored `runs/` and `.evotrias/`
directories. Each completed run includes machine-readable results and a concise
Markdown summary.

## Defaults and Runtime Switches

| Setting | Default | Effect |
| --- | ---: | --- |
| `LINEAGE_ROOT_NUMBER` | 4 | Initial exploration directions, number of root exploration branches |
| `MAX_CONCURRENT_TASKS` | 4 | Rolling scheduler queue limit |
| `MAX_TOTAL_TASKS` | 50 | Hard task budget |
| Plateau window | 5 successes | Window used for plateau diagnostics |
| Plateau patience | 4 | Consecutive plateau count required before strategy adaptation |
| Fitness improvement threshold | 0.0 | A positive running-best improvement is progress |
| Meta-guidance items per request | 3 | Maximum distinct guidance items generated |
| Uses per guidance item | 2 | Maximum tasks assigned to each item |
| Retained tried guidance | 10 | Outcome history available to later guidance requests |

The three scheduling values above can be stated in the evolve request (for
example "6 initial directions, 8 in parallel, 200 tasks"); they will be confirmed before 
creating the run and locked when a run is initialized.

Set these environment variables before starting the MCP process; they are
resolved once by `configure_run` and frozen into the run's `config.json`:

```powershell
# Disable meta-guidance while retaining plateau diagnostics and normal selection.
$env:EVOTRIAS_META_EVOLVE_ENABLED = "0"

# Disable run-local Notes.
$env:EVOTRIAS_NOTES_ENABLED = "0"

# Disable automatic dashboard startup.
$env:EVOTRIAS_UI_ENABLED = "0"

# Treat relative best-fitness improvement below 1% as plateau.
$env:EVOTRIAS_PERF_IMPROVEMENT_THRESHOLD = "0.01"
```

Hard task and time budgets remain active independently of plateau detection.

For remote access, set these before starting EvoTrias:

```bash
export EVOTRIAS_UI_HOST=0.0.0.0
export EVOTRIAS_UI_PUBLIC_HOST="<HOST_OR_IP>"
```

Use the URL printed by EvoTrias. Leave these variables unset for local-only access.

## Run Artifacts

EvoTrias separates the agent workspace from controller-owned state. Both are
relative to the working directory:

```text
runs/<run_id>/                                        agent workspace
  tasks/<task_id>/direction.txt                       written by init-explorer
  tasks/<task_id>/attempts/<attempt_id>/solution.py   written by task-executor (or solution.cpp)
  tasks/<task_id>/attempts/<attempt_id>/eval_result.json
  run_summary.md                                      written by finalize_run
  run_summary.json
```

```text
.evotrias/
  db/runs/<run_id>/   controller state (config, run state, attempts, notes, meta-guidance, task pool)
  hooks/              hook-private state
  locks/              cross-process locks
  logs/               MCP process logs
```

The per-run state files are listed in [docs/mcp_interface.md](docs/mcp_interface.md)
(section 3). Both locations are local runtime state and are ignored by Git. Share
the final summaries or a curated benchmark report instead of committing complete
run directories.

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall evotrias evotrias_plugin benchmarks
```

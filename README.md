# AgentEvolve 🧬

AgentEvolve is an agent-agnostic control plane for evolutionary program
search. It combines an Evolution Controller MCP, a Claude Code workflow
plugin, and benchmark-owned evaluators. The controller owns selection,
lineage, run-local memory, convergence state, and meta-evolve state; coding
agents generate candidates and benchmark evaluators determine their fitness.

Fitness is task-generic and always maximized. Benchmark-specific metrics such
as loss, runtime, or distance remain inside the evaluator result.

## What It Does ✨

- Seeds each run with four distinct root directions. When a benchmark provides
  \`initial_program.py\`, root directions and root candidates use it as a
  reference while still allowing substantially different approaches.
- Keeps a rolling queue of at most four task-executor agents. Initial tasks
  receive up to 10 verification attempts; replenishment tasks receive up to 3.
- Persists successful candidates as a lineage-aware success pool, then selects
  a parent and complementary inspirations for each later task.
- Maintains run-local Notes distilled from verified attempts and injects the
  relevant notes into later task contexts.
- Detects a plateau and enters \`WATCHING\`; a plateau never stops a run. The
  controller either requests meta-guidance immediately or temporarily increases
  exploration before requesting it.
- Runs meta-evolve as a bounded guidance lifecycle: the host agent produces up
  to three distinct guidance items, each is assigned to at most two tasks, and
  the MCP records their outcomes for later guidance generation.
- Stops automatic replenishment only for a hard task/time budget or an external
  stop, then writes deterministic run summaries.

## Architecture 🧭

\`\`\`text
Claude Code orchestrator
  |  initializes a run, maintains the queue, generates meta-guidance JSON
  v
Evolution Controller MCP
  |  parent/inspiration selection, Notes, convergence, meta-guidance,
  |  attempt recording, lineage state, final reports
  v
task-executor agents
  |  read selected context, generate one candidate, run the benchmark verifier
  v
benchmark skills and evaluators
  |  write eval_result.json with a normalized fitness
  +--> Evolution Controller MCP
\`\`\`

The controller keeps internal state out of agent context. Agents exchange
stable IDs and artifact references through compact MCP responses; the MCP
recovers parent, inspiration, note, and meta-guidance context from disk.

## Repository Layout 🗂️

\`\`\`text
agent_evolve_mcp/       Evolution Controller MCP and persistence layer
agent_evolve_plugin/    Claude Code skills and task-executor agents
benchmarks/             18 benchmark skills, evaluators, and reference programs
tests/                  Controller and benchmark regression tests
runs/                  Per-run candidate artifacts and final reports (gitignored)
.agent_evolve/         Per-run controller state (gitignored)
\`\`\`

## Setup ⚙️

The math benchmarks require the \`math\` extra. The \`dev\` extra provides pytest.

\`\`\`powershell
uv sync --extra math --extra dev
.\.venv\Scripts\Activate.ps1
uv run python benchmarks/install.py --all
\`\`\`

\`benchmarks/install.py\` adds the benchmark skills and their evaluator-command
patterns to \`.claude/settings.local.json\`. It does not grant broad shell
permissions. Run it again after adding a benchmark.

## Run a Benchmark ▶️

Start Claude Code from the repository root with both plugins:

\`\`\`powershell
claude --plugin-dir agent_evolve_plugin --plugin-dir benchmarks
\`\`\`

Then submit a task such as:

\`\`\`text
/agent_evolve_plugin:evolve run the third_autocorr_ineq benchmark end to end for 50 tasks.
\`\`\`

The orchestrator creates a run ID such as \`run_20260710143000\`, configures the
hard task budget before dispatching any task, waits for four root directions,
and fills the queue. The original task text is passed unchanged to each
subagent.

For each task, the executor:

1. asks MCP for a parent, inspirations, run-local Notes, and any assigned
   meta-guidance;
2. reads the selected artifacts and, for root tasks, the benchmark's
   \`initial_program.py\` when present;
3. generates one candidate per attempt and runs the benchmark-defined
   evaluator;
4. records the evaluator result with MCP and lets MCP update the success pool,
   Notes requests, lineage state, and meta-guidance attribution.

## Meta-Evolve 🧠

\`WATCHING\` is entered only after the convergence detector has enough successful
records and sees a sustained plateau. It is a search-mode transition, not a
stop condition.

With the default \`watch_tasks_before_guidance: 0\`, entering \`WATCHING\` directly
returns \`action="request_meta_guidance"\`. The orchestrator asks MCP for a
request payload, then produces structured guidance using the current-best
candidate, plateau diagnostics, lineage summaries, run-local Notes, and
outcomes from prior guidance. During this explicit generation step it may read
the verifier/evaluator source to understand scoring rules, but it must not
modify or bypass it.

MCP validates the guidance, rejects duplicate \`approach_type\` values in a
batch, and assigns guidance to later tasks. Guidance is anchored to the latest
canonical best when a task is selected. A guidance item may refine that parent
or explicitly depart from or replace its strategy; task executors must not
force an incoherent parent/inspiration hybrid. Once an item has used its budget,
MCP stores whether it succeeded, created a new best, and its measured
improvement. Future guidance requests receive that history.

Set \`watch_tasks_before_guidance\` above zero only when an experiment needs the
first-level \`WATCHING\` exploration boost before requesting guidance. In that
mode the controller temporarily increases parent-selection temperature,
cross-lineage migration, and inspiration sampling temperature while waiting.

## Defaults and Runtime Switches 🎛️

| Setting | Default | Effect |
| --- | ---: | --- |
| Initial directions | 4 | Number of root exploration branches |
| Maximum concurrent tasks | 4 | Rolling scheduler queue limit |
| Default task budget | 50 | Used when the request does not specify a task count |
| Convergence window | 5 successes | Window used for plateau diagnostics |
| Plateau patience | 4 | Consecutive plateau count required for \`WATCHING\` |
| Fitness improvement threshold | 0.0 | A positive running-best improvement is progress |
| Meta-guidance items per request | 3 | Maximum distinct guidance items generated |
| Uses per guidance item | 2 | Maximum tasks assigned to each item |
| Retained tried guidance | 10 | Bounded outcome history used by later requests |

The controller loads these defaults when \`evolve_configure_run\` is called for a
new run. That configuration is locked for the run. The following environment
variables must be set before the MCP process starts:

\`\`\`powershell
# Disable plateau detection and therefore meta-guidance requests.
$env:AGENT_EVOLVE_CONVERGENCE_ENABLED = "false"

# Disable meta-guidance request creation while retaining normal selection.
$env:AGENT_EVOLVE_META_EVOLVE_ENABLED = "0"

# Disable run-local Notes.
$env:AGENT_EVOLVE_NOTES_ENABLED = "0"

# Treat relative best-fitness improvement below 1% as plateau.
$env:AGENT_EVOLVE_PERF_IMPROVEMENT_THRESHOLD = "0.01"

# Place controller state outside the repository default location.
$env:AGENT_EVOLVE_CONTROLLER_DIR = "D:\agent-evolve-state"
\`\`\`

Hard budgets remain active when convergence detection is disabled. A completed
run has \`action="budget_exhausted"\` only when its task or optional time budget
is exhausted; \`should_stop\` is retained for compatibility and is true only in
that case.

## Run Artifacts 📁

\`runs/<run_id>/\` contains candidate artifacts and reports owned by the plugin:

\`\`\`text
runs/<run_id>/
  completed_tasks.jsonl
  scheduler_state.json
  tasks/<task_id>/attempts/<attempt_id>/solution.py
  tasks/<task_id>/attempts/<attempt_id>/eval_result.json
  run_summary.md
  run_summary.json
\`\`\`

\`.agent_evolve/controller/<run_id>/\` contains controller-owned state:

\`\`\`text
success_db.json                         reusable successful candidates
attempts.jsonl                          append-only verifier audit log
task_contexts.json                      cached parent/inspiration/task context
run_state.json                          search state and diagnostics
notes.json                              run-local distilled Notes
meta_guidance/meta_guidance_state.json  active and tried guidance lifecycle
meta_guidance/meta_guidance_uses.jsonl  assignment and outcome events
\`\`\`

Both directories are local runtime state and are ignored by Git. Share
\`run_summary.md\`, \`run_summary.json\`, or a curated benchmark report rather than
committing complete run directories.

## Local Checks ✅

\`\`\`powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m compileall agent_evolve_mcp agent_evolve_plugin benchmarks
\`\`\`

The current test suite covers initial-program migration and the meta-evolve
lifecycle, including request generation, guidance validation, assignment,
outcome attribution, and reset behavior.

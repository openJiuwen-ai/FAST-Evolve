<div align="center">

<img src="../assets/evotrias-banner.png" alt="EvoTrias" width="500">

**Program evolution for coding agents**

[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](../LICENSE)
[![Release](https://img.shields.io/badge/release-GitHub-181717)](https://github.com/openJiuwen-ai/FAST-Evolve/releases)
[![Project Page](https://img.shields.io/badge/project-evotrias.github.io-6E56CF)](https://evotrias.github.io/)

**English** | [中文](https://gitcode.com/openJiuwen/FAST-Evolve/tree/main/evo_trias) | [Website](https://evotrias.github.io/)

</div>

## Overview

**EvoTrias** is a program evolution plugin for coding agents. Given an optimization task and an evaluator, it drives an existing coding agent to generate, evaluate, and select candidate programs continuously, iterating from the current best solution.

## Key Features

- ⚖️ **Separation-of-powers architecture**: The three decisions in program evolution — govern, propose, and judge — are owned independently by three planes: Structured Governance, Open Proposal, and Protected Judgment. The planes interact only through typed contracts, and runtime hooks keep each within its own authority.
- 🔌 **Plugin integration**: Deployed into a workspace as skills, an MCP server, and hooks. Without modifying the coding agent itself, EvoTrias gives it multi-direction, cross-round evolution while fully preserving its autonomous development capability on large codebases.
- 🧭 **Evidence-driven strategy search**: The controller distills evaluation outcomes into multi-level evidence and adaptively selects parent and inspiration candidates, moving the search from broad to deep as the budget progresses. On stagnation, it generates Meta-guidance to escape the plateau; useful single-attempt experience is distilled into Notes for later tasks to reuse. Search state is persisted by the controller and stays out of the agent's context.
- 🛡️ **Evaluator isolation**: The task contract is public; the evaluator is private. Permission hooks block access to evaluator sources and other tasks' results at the tool-call level, and candidates receive only normalized validity, fitness, and metrics, so scores faithfully reflect solution quality.
- 📊 **Real-time observability**: Each run automatically launches a read-only dashboard showing fitness progress, lineage, active tasks, Notes, and Meta-guidance. On completion, a structured result and a Markdown summary are generated.

### Supported Coding Agents

| Coding agent | Status | Notes |
| --- | :---: | --- |
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | ✅ Full support | Scheduler and permission hooks fully in effect; suited to long unattended runs |
| [OpenCode](https://opencode.ai) | ✅ Supported | Core evolution workflow fully available; permission hooks in progress |
| [Codex](https://github.com/openai/codex) | 🗓️ Planned | — |
| [JiuwenSwarm](https://github.com/openJiuwen-ai/jiuwenswarm) | 🗓️ Planned | — |

## Documentation

<!-- TODO: links to in-repo docs, examples, and Release -->

## Requirements

| Item | Requirement |
| --- | --- |
| OS | Linux, macOS, or Windows |
| Python | ≥ 3.11 |
| Coding agent | See [Supported Coding Agents](#supported-coding-agents) |

## Installation

```bash
git clone https://github.com/openJiuwen-ai/FAST-Evolve.git
pip install ./FAST-Evolve/evo_trias
```

Run the following command to verify the installation; it should print the evotrias version:

```bash
evotrias --version
```

## Quick Start

This section uses the built-in Circle Packing benchmark to walk through a complete evolution run. The task is to place 26 non-overlapping circles in a unit square so that the sum of their radii is maximized.

**1. Install task dependencies**

```bash
pip install -r FAST-Evolve/benchmarks/requirements/math.txt
```

**2. Initialize a workspace**

```bash
mkdir my-workspace && cd my-workspace
evotrias setup
```

Follow the prompts to select a coding agent (Claude Code or OpenCode) and complete the installation. This installs the subagents, skills, MCP configuration, and hooks into the current directory.

**3. Import the task to evolve**

Run only the command below that matches the coding agent selected in the previous step:

```bash
# Claude Code
cp -r ../FAST-Evolve/benchmarks/skills/circle-packing .claude/skills/
# OpenCode
cp -r ../FAST-Evolve/benchmarks/skills/circle-packing .opencode/skills/
```

Tasks are provided as skills and go into the skills directory of the coding agent selected in the previous step.

**4. Start evolution**

```bash
evotrias
```

In the launched session, submit the evolution request through the `evolve` skill:

```text
/evolve evolve circle-packing, 2 initial directions, 2 in parallel, 10 tasks
```

Once the run parameters are confirmed, EvoTrias creates a run directory under `runs/` and prints the dashboard URL. It then explores several initial directions in parallel and keeps evolving from the current best solution until the task budget is exhausted. The run summary is written to `runs/<run_id>/run_summary.md`.

> [!TIP]
> To optimize your own problem, write it as a skill: describe the optimization objective, the output path of candidate programs, and how to invoke the evaluator in `SKILL.md`, then place it in the workspace's skills directory. The benchmark tasks under [`benchmarks/skills/`](../benchmarks/skills/) serve as references.

## License

This project is licensed under the [Apache License 2.0](../LICENSE).

This product serves solely as a workflow orchestration tool and does not embed any AI model capabilities. When users integrate AI models for specific business scenarios, they shall bear full responsibility for compliance obligations under the EU AI Act and other relevant regulatory frameworks.

---

Developed by Taylor Lab, Huawei Technologies Co., Ltd.

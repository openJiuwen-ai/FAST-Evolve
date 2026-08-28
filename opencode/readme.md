# EvoTrias on OpenCode

Run EvoTrias evolution experiments through [opencode](https://opencode.ai).

## Setup

Run **in this directory**:

```
python prepare.py
```

Then set one environment variable:

```powershell
# Windows (PowerShell)
$env:OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS="true"
```

```bash
# Linux / macOS
export OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true
```

## Run an experiment

Start opencode **in this directory**:

```
opencode
```

Inside opencode, launch an experiment with the `/evolve` command, e.g.:

```
/evolve solve circle packing, with max task number is 5, concurrency is 2, root number is 3
```

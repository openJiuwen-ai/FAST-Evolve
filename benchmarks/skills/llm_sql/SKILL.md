---
name: llm_sql
description: Use when the user wants to solve or evolve the LLM-SQL prefix-caching column-reordering benchmark.
argument-hint: [run_id] [task_id] [artifact_ref]
---

# LLM-SQL Column Reordering

Evolve a row-adaptive column ordering that maximizes prefix reuse when table
rows are serialized into LLM prompts. The evaluator combines prefix-hit rate
with bounded execution time; higher fitness is better.

## Evaluator

Use the standard attempt-local artifact contract:

```text
main_artifact_name = solution.py
artifact_ref = runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/solution.py
```

Run only:

```bash
uv run python benchmarks/skills/llm_sql/scripts/evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
```

The candidate must preserve all rows and columns. A successful evaluation
returns exit code 0 and writes the standard result JSON.

## Program interface

Keep the `Evolved` class and its `reorder` method compatible with the reference
program. The method returns `(reordered_dataframe, column_orderings)` and must
not add or remove rows or columns. `initial_program.py`, `scripts/solver.py`,
and `scripts/utils.py` provide the reference implementation and scoring helper.

## Required data

Place these files under `datasets/` in this skill directory:

- `movies.csv`
- `beer.csv`
- `BIRD.csv`
- `PDMX.csv`
- `products.csv`

These external datasets are intentionally not checked into the repository. The
evaluator reports all missing paths instead of silently skipping a dataset. Download
them once with:

```bash
uv run python benchmarks/skills/llm_sql/scripts/download_data.py
```

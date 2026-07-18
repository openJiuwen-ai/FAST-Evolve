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

## Problem definition and optimization guidance

You are an expert in data optimization and LLM prompt caching. Your task is to
evolve the existing `Evolved` class to maximize prefix hit count (PHC) for
efficient LLM prompt caching.

### Problem context

- You are given a pandas DataFrame `df` with text data in rows and columns.
- The goal is to reorder columns to maximize prefix reuse when processing rows
  sequentially.
- Prefix reuse occurs when consecutive rows have matching values in the same
  column positions.
- This reduces LLM computation costs by reusing cached prefixes.

### Objective

- Dual objective: (1) maximize prefix reuse across consecutive rows and (2)
  minimize end-to-end runtime of the algorithm.
- When the LLM processes each row sequentially, it should reuse as much of the
  prefix from the previous row as possible while keeping the algorithm
  computationally efficient.
- Prefix reuse is defined as consecutive field values, starting from the first
  column, that are exact matches with the corresponding fields of the previous
  row.
- The hit score of a row is the sum of squares of the string lengths of the
  matching prefix fields.
- The algorithm is evaluated on a combined metric balancing accuracy and speed.

Formally, for a column ordering `C`:

- `PHC(C)` is the sum over all rows `r` of `hit(C, r)`.
- `hit(C, r)` is the sum of `len(df[r][C[f]])^2` for all prefix fields where
  `df[r][C[f]] == df[r-1][C[f]]`; it is zero when the first field mismatches.
- Runtime is wall-clock seconds to compute the reordered DataFrame.
- The selection score is:
  `combined_score = 0.95 * average_hit_rate + 0.05 * (12 - min(12, average_runtime)) / 12`.

### Required API and constraints

Keep the existing `Evolved` class structure and this `reorder` signature:

```python
class Evolved(Algorithm):
    def reorder(
        self,
        df: pd.DataFrame,
        early_stop: int = 0,
        row_stop: int = None,
        col_stop: int = None,
        col_merge: List[List[str]] = [],
        one_way_dep: List[Tuple[str, str]] = [],
        distinct_value_threshold: float = 0.8,
        parallel: bool = True,
    ) -> Tuple[pd.DataFrame, List[List[str]]]:
```

- Internal implementations may change, but the class structure and method
  signatures must be preserved.
- `reorder` must return `(reordered_dataframe, column_orderings)`.
- Do not add or remove rows or columns.
- Use different column orderings for different rows to maximize prefix hit rate.
- Return a DataFrame with the same shape as the input.
- Use exact string matching for prefix calculations.
- Keep memory usage reasonable for large datasets.
- The algorithm is called with the same parameters as the original `Evolved`.
- Handle missing values and mixed data types appropriately.

### Algorithm design guidance

- For each row, determine an optimal column order based on matches with the
  previous row.
- Consider column statistics, including unique values and string lengths.
- Use greedy or heuristic approaches for scalability.
- Focus on columns with high value frequency and long strings.
- Optimize the existing recursive approach or replace it with efficient
  vectorized methods.
- Consider prefix-aware greedy approaches conditioned on the current matched
  prefix.

Return the optimized `Evolved` class without explanations.

## Required data

Place these files under `datasets/` in this skill directory:

- `movies.csv`
- `beer.csv`
- `BIRD.csv`
- `PDMX.csv`
- `products.csv`

These external datasets are intentionally not checked into the repository. The
A missing dataset is skipped during the evaluation loop, and the evaluation
fails when not all files are processed. Download
them once with:

```bash
uv run python benchmarks/skills/llm_sql/scripts/download_data.py
```

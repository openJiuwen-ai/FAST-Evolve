---
name: matmul
description: Use when user wants to solve matmul problems, or execute matmul tasks.
argument-hint: [run_id] [task_id] [artifact_ref]
---

# Matrix Multiplication Tensor Decomposition (n=2, m=4, p=5)

Matrix multiplication tensor decomposition (n=2, m=4, p=5) - find the lowest-rank decomposition of the matrix multiplication tensor. Beat the AlphaEvolve rank of 32.

You are an expert in computational linear algebra, numerical optimization, and AI-driven algorithm discovery. Your task is to evolve and optimize a Python script to find the lowest-rank decomposition of the matrix multiplication tensor for a specific instance with variables (n=2, m=4, p=5) fixed.

## Evaluator

**IMPORTANT**: To evaluate a program, run the evaluator script directly:

```bash
uv run python benchmarks/skills/matmul/scripts/evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
```

For the self-evolution workflow, evaluator success means the candidate is valid
and the evaluator exits with code 0 / writes `"status": "success"`. The
AlphaEvolve value is a reference objective, not a pass/fail threshold. If the
evaluator succeeds, stop this task attempt and record it through MCP; do not
keep editing and rerunning the same attempt just because the fitness could be
higher.

Do not run extra shell commands to probe the Python environment. The verifier
command above is the only execution command needed by task-executor.

## Problem Context

- Target: Find the minimal rank R for the tensor decomposition T_ijk = ∑_r=1^R U_ir V_jr W_kr
- Matrix sizes: n=2, m=4, p=5
- Current best: rank 32 (from Google)
- Goal: Find a lower rank decomposition (lower is better for rank, but score is BENCHMARK/R = 32/R)

## Task

Design a constructor that returns a high-quality decomposition; choose the optimization strategy that best fits the tensor factorization problem.

## Program Interface

Your program must implement a `run()` function that returns:
- `decomposition`: tuple of 3 numpy arrays (U, V, W)
- `n`: int (should be 2)
- `m`: int (should be 4)
- `p`: int (should be 5)
- `loss`: float - final optimization loss
- `rank`: int - rank of decomposition

## Constraints

- The function should be deterministic (use fixed seeds if using random methods)

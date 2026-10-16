---
name: third-autocorr-ineq
description: Use when user wants to solve third_autocorr_ineq problems, or execute third_autocorr_ineq tasks.
---

# Third Autocorrelation Inequality Problem

Third autocorrelation inequality - find a real-valued function f to minimize
the constant C3 in the autocorrelation inequality. Beat the AlphaEvolve
benchmark of C3 <= 1.4556427953745406.

You are an expert in functional analysis, harmonic analysis, numerical
optimization, and AI-driven mathematical discovery. Your task is to evolve and
optimize a Python script to find a better upper bound for the third
autocorrelation inequality constant C3.

## Evaluator

When this benchmark is used by `evotrias-task-executor`, use the standard attempt-local
artifact contract:

```text
main_artifact_name = solution.py
artifact_ref = runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/solution.py
```

In the commands below, `<skill_dir>` is the directory containing this SKILL.md.

**IMPORTANT**: To evaluate a program, run the evaluator script directly:

```bash
python <skill_dir>/scripts/evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
```

For the self-evolution workflow, evaluator success means the candidate is valid
and the evaluator exits with code 0 / writes `"status": "success"`. The
AlphaEvolve value is a reference objective, not a pass/fail threshold. If the
evaluator succeeds, stop this task attempt and record it through MCP; do not
keep editing and rerunning the same attempt just because the fitness could be
higher.

Do not run extra shell commands to probe the Python environment. The verifier
command above is the only execution command needed by evotrias-task-executor.

## Optimization Direction

This is a minimization problem for the raw mathematical value:

- Lower `c3_achieved` is better.
- The evaluator converts that minimization objective into the standard
  higher-is-better fitness used by the evolution controller.
- `fitness = 1.4556427953745406 / c3_achieved`.
- A fitness above `1.0` means the candidate improves on the AlphaEvolve
  reference bound.

Do not optimize for a larger reported C3. Optimize the implementation so that
the recomputed C3 value is as small as possible while remaining valid.

## Problem Context

**Target**: Find a real-valued function f: R -> R that minimizes the upper
bound of the constant C3 in the inequality:

```text
max_{-1/2 <= t <= 1/2} |f * f(t)| <= C3 (integral_{-1/4}^{1/4} f(x) dx)^2
```

This is equivalent to minimizing:

```text
C3 = max |f * f| / (integral f)^2
```

Current best known bound:
- literature: C3 <= 1.45810
- AlphaEvolve: C3 <= 1.4556427953745406

Goal: Beat the AlphaEvolve upper bound of 1.4556427953745406.

## Task

Bounded computation and scientific-computing libraries such as NumPy/SciPy/jax/optax are allowed when they help.

## Program Interface

Your program must implement a `run()` function that returns a 4-tuple, not a
dict:
- `f_values`: numpy array of shape `(n_points,)` - discretized function values
- `c3_achieved`: float - the computed C3 constant
- `loss`: float - the optimization loss value
- `n_points`: int - number of points used in the integral interval

```python
def run():
    ...
    return f_values, c3_achieved, loss, n_points
```

Do not return `{"f_values": ..., "c3_achieved": ...}`. The evaluator unpacks
the return value positionally.

## C3 Computation

Report `c3_achieved` using the same rectangle-rule computation as the
evaluator:

```python
dx = 0.5 / n_points
integral_sq = (np.sum(f_values) * dx) ** 2
conv = np.convolve(f_values, f_values, mode="full") * dx
c3_achieved = np.max(np.abs(conv)) / integral_sq
```

Do not use trapezoidal integration for the reported value. A candidate may use
other approximations internally while searching, but the final reported
`c3_achieved` must be recomputed exactly as above.

## Key Insights

- Lower C3 is better (minimization problem)
- Fitness is still higher-is-better because the evaluator uses
  `benchmark_c3 / c3_achieved`
- Unlike the first and second autocorrelation problems, f may take positive and
  negative values
- The integral over `[-1/4, 1/4]` must be non-zero
- The evaluator recomputes C3 with `numpy.convolve` and `numpy.abs`
- FFT can efficiently compute the autoconvolution during candidate generation

## Constraints

- The function should be deterministic (use fixed seeds if using random methods)
- `f_values` must be finite and have shape `(n_points,)`
- The reported `c3_achieved` must match the evaluator's recomputed value

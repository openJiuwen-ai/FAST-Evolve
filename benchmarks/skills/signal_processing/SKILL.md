---
name: signal_processing
description: Real-time adaptive signal processing - filter volatile, non-stationary time series data using a sliding window approach. Optimize for smoothness, tracking accuracy, correlation, and noise reduction.
argument-hint: [run_id] [task_id] [artifact_ref]
---

# Real-Time Adaptive Signal Processing

You are an expert signal processing engineer specializing in real-time adaptive filtering algorithms. Your task is to improve a signal processing algorithm that filters volatile, non-stationary time series data using a sliding window approach.

## Evaluator

**IMPORTANT**: To evaluate a program, run the evaluator script directly:

```bash
uv run python benchmarks/skills/signal_processing/scripts/evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
```

For the self-evolution workflow, evaluator success means the candidate is valid
and the evaluator exits with code 0 / writes `"status": "success"`. The
AlphaEvolve value is a reference objective, not a pass/fail threshold. If the
evaluator succeeds, stop this task attempt and record it through MCP; do not
keep editing and rerunning the same attempt just because the fitness could be
higher.

Do not run extra shell commands to probe the Python environment. The verifier
command above is the only execution command needed by task-executor. Candidate
solutions should rely on Python standard library and numpy unless the benchmark
explicitly declares another dependency; do not assume scipy is available.

## Problem Context

The algorithm must minimize noise while preserving signal dynamics with minimal computational latency and phase delay. Focus on the multi-objective optimization of:
1. Slope change minimization - reducing spurious directional reversals
2. Lag error minimization - maintaining responsiveness
3. Tracking accuracy - preserving genuine signal trends
4. False reversal penalty - avoiding noise-induced trend changes

## Task

Focus on designing an explicit constructor rather than an iterative search algorithm.

## Program Interface

Your program must implement a `run_signal_processing(noisy_signal, window_size)` function that returns:
- A dict containing:
  - `filtered_signal`: 1D array of filtered output
  - `correlation`: correlation with clean signal (if available)
  - `noise_reduction`: noise reduction ratio (if available)

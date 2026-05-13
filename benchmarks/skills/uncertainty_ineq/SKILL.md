---
name: uncertainty_ineq
description: Uncertainty inequality - Find Hermite polynomial coefficients minimizing the largest positive root to bound C4. AlphaEvolve achieved C4 = 0.3216.
argument-hint: [run_id] [task_id] [artifact_ref]
---

# Uncertainty Inequality Problem

You are an expert mathematician specializing in polynomial analysis and optimization. Your task is to find Hermite polynomial coefficients that minimize C4 in the uncertainty inequality bound.

## Evaluator

When this benchmark is used by `task-executor`, use the standard attempt-local artifact contract:

```text
main_artifact_name = solution.py
artifact_ref = runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/solution.py
```

**IMPORTANT**: To evaluate a program, run the evaluator script directly:

```bash
uv run python benchmarks/skills/uncertainty_ineq/scripts/evaluator.py \
  --run-id <run_id> \
  --task-id <task_id> \
  --artifact-ref <artifact_ref> \
  --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
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

## Task

Find coefficients for Hermite polynomials that minimize C4 where:
- P(x) = c₀*H₀(x) + c₁*H₄(x) + ... + cₘ*H₄ₘ(x)
- P(0) = 0 (forced by construction)
- C4 = r²_max / (2π) where r_max is the largest positive root of P(x)/x²

The AlphaEvolve paper achieved C4 = 0.3216.

## Key Insights

- Use Hermite polynomials of degree 4k for even symmetry
- Force P(0) = 0 by solving for the last coefficient
- Sympy provides exact polynomial manipulation and root finding
- Higher-degree polynomials may achieve better bounds

## Program Interface

Your program must implement a `run()` function:

```python
import numpy as np

def run():
    """
    Returns:
        coeffs: np.ndarray - polynomial coefficients
        c4_bound: float - the C4 bound you believe you achieved
        r_max: float - the largest positive root
    """
    # Your optimization code here
    return coeffs, c4_bound, r_max
```

## Constraints

- All coefficients must be real numbers
- P(0) = 0 (evaluator will verify)
- r_max must be a valid positive root
- The function should be deterministic (use fixed seeds if using random methods)

## Scoring

- **combined_score** = 0.3216 / achieved_c4
- Score > 1.0 means improvement over AlphaEvolve benchmark
- Lower C4 is better

## Dependencies

- numpy
- sympy (for exact polynomial verification)
- jax, jax.numpy, optax (for optimization)

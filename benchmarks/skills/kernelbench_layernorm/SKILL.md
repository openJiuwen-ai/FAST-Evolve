---
name: kernelbench_layernorm
description: Use when user wants to solve kernelbench_layernorm problems, or execute kernelbench_layernorm tasks.
argument-hint: [run_id] [task_id] [artifact_ref]
---

# Layer Normalization Kernel Optimization

Optimize a Triton/CUDA kernel for Layer Normalization (KernelBench Level 1 Problem 40) - achieve speedup over PyTorch eager execution.

You are an expert Triton engineer specializing in GPU kernel optimization. Your task is to implement a high-performance Layer Normalization kernel that achieves speedup over PyTorch eager execution.

## Evaluator

When this benchmark is used by `task-executor`, use the standard attempt-local artifact contract:

```text
main_artifact_name = solution.py
artifact_ref = runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/solution.py
```

For each attempt, write the candidate program to that exact `artifact_ref`.
The `attempt_id` comes from the task-executor workflow and must be one of
`attempt_001`, `attempt_002`, or `attempt_003`.
Do not write the candidate program to `benchmarks/outputs` for task-executor
runs. The verifier evaluates the attempt-local artifact path directly.

**IMPORTANT**: To evaluate a program, run the evaluator script directly:

```bash
uv run python benchmarks/skills/kernelbench_layernorm/scripts/evaluator.py --run-id <run_id> --task-id <task_id> --artifact-ref <artifact_ref> --out runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/eval_result.json
```

The arguments are parsed from the skill invocation:
- `run_id`: The run identifier (e.g., "kernelbench_layernorm_test")
- `task_id`: The task identifier (e.g., "task_001")
- `artifact_ref`: The attempt-local program file path, normally `runs/<run_id>/tasks/<task_id>/attempts/<attempt_id>/solution.py`

The evaluator will:
1. Load the candidate program from `artifact_ref`
2. Run correctness checks (5 trials) against PyTorch reference
3. Benchmark performance (100 trials) against PyTorch eager and torch.compile baselines
4. Calculate speedup = eager_time / kernel_time
5. Save results to the `--out` path
6. Print the result file path on success, error message on failure
7. Include the same `artifact_ref` in the result JSON so Evolution Controller MCP can record the attempt.

For the self-evolution workflow, evaluator success means the candidate passes all
correctness tests and the evaluator exits with code 0 / writes `"status": "success"`.
A higher fitness (speedup) indicates better performance. If the evaluator succeeds,
stop this task attempt and record it through MCP; do not keep editing and
rerunning the same attempt just because the fitness could be higher.

Do not run extra shell commands to probe the Python environment. The verifier
command above is the only execution command needed by task-executor.

## Problem Description

Layer Normalization normalizes the input tensor along the last dimensions. Given input tensor `x` of shape `(batch_size, features, dim1, dim2)`, LayerNorm computes for each sample:

```
mean = x.mean(dim=(-3, -2, -1), keepdim=True)
var = x.var(dim=(-3, -2, -1), keepdim=True, unbiased=False)
y = (x - mean) / sqrt(var + eps) * weight + bias
```

where `eps=1e-5`, `weight` and `bias` are learnable parameters of shape `(features, dim1, dim2)`.

## Reference Implementation

```python
import torch
import torch.nn as nn

class Model(nn.Module):
    def __init__(self, normalized_shape: tuple):
        super(Model, self).__init__()
        self.ln = nn.LayerNorm(normalized_shape=normalized_shape)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.ln(x)

batch_size = 16
features = 64
dim1 = 256
dim2 = 256

def get_inputs():
    x = torch.rand(batch_size, features, dim1, dim2)
    return [x]

def get_init_inputs():
    return [(features, dim1, dim2)]
```

## Program Interface

Your program must define a `class ModelNew` that inherits from `Model` and overrides the forward method with an optimized implementation:

```python
import torch
import torch.nn as nn

class ModelNew(Model):
    def __init__(self, normalized_shape: tuple):
        super().__init__(normalized_shape)
        # Setup custom kernel resources here

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Optimized implementation using Triton/CUDA kernels
        ...
```

## Key Optimization Insights

- LayerNorm is **memory-bandwidth bound** — the arithmetic intensity is low (one pass for mean, one for variance, one for normalize)
- **Fusing** the mean/variance/computation into a single kernel pass avoids 3x global memory round trips
- The normalized shape `(64, 256, 256)` = 4,194,304 elements per sample — large enough to benefit from parallel reduction
- Row-based parallelism (one CUDA block per sample) is a natural tiling strategy
- Online variance computation (Welford's algorithm) allows single-pass fused kernel
- Consider using `tl.dot` for vectorized reductions when the last dimension is large
- Weight/bias fusion into the normalize kernel saves an extra elementwise pass

## Scoring

- **combined_score** / **fitness**: Speedup over PyTorch eager execution (primary metric, higher is better)
- **speedup_over_compile**: Speedup over torch.compile() (secondary metric)
- **kernel_time_ms**: Execution time of optimized kernel (lower is better)
- **ref_eager_time_ms**: Reference eager execution time for comparison

## Constraints

- Output must be numerically correct (within floating-point tolerance: `torch.allclose` with default rtol/atol)
- Code must run on CUDA GPU
- Input shape: `(16, 64, 256, 256)`, normalized shape: `(64, 256, 256)`

## Dependencies
- Python packages: `torch`, `triton` (optional)
- GPU with CUDA support

# Dataset provenance

The public problem statements and initial C++ stubs under `problems/` were
copied from:

```text
repository: https://github.com/PsyAbyss/CORAL.git
revision: 7d6df442f86d2e1bd53bd5f12c7b381d6b7b7430
path: examples/frontier_cs_algo/<problem_id>/seed/
```

Per-problem names and runtime metadata were extracted from the sibling
`task.yaml` files. `suite_manifest.json` records the resulting problem IDs and
SHA-256 hashes. The import deliberately excludes CORAL's grader package because
this benchmark uses one shared AgentEvolve evaluator.

The source files are copied byte-for-byte. This preserves the exact public
task material in the referenced CORAL checkout, including any source-side text
encoding artifacts.

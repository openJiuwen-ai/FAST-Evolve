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
this benchmark uses one shared EvoTrias evaluator.

Problem statements and initial C++ stubs are copied byte-for-byte, preserving
source-side text encoding artifacts. The hashes in `metadata.json` and
`suite_manifest.json` describe these unmodified local copies.

The repository `.gitattributes` marks `statement.txt` and
`initial_program.cpp` as `-text` so Git does not rewrite their original line
endings during checkout or commit.

#!/usr/bin/env python
"""Import Frontier-CS algorithmic public task packs from a CORAL checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import yaml

SKILL_DIR = Path(__file__).resolve().parents[1]
PROBLEMS_DIR = SKILL_DIR / "problems"
MANIFEST_PATH = SKILL_DIR / "suite_manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _numeric_problem_dirs(source: Path) -> list[Path]:
    return sorted(
        (path for path in source.iterdir() if path.is_dir() and path.name.isdigit()),
        key=lambda path: int(path.name),
    )


def _load_task(task_path: Path) -> dict[str, Any]:
    with task_path.open("r", encoding="utf-8") as source:
        task = yaml.safe_load(source)
    if not isinstance(task, dict):
        raise ValueError(f"Invalid task YAML: {task_path}")
    return task


def import_tasks(source: Path, *, source_repository: str, source_revision: str) -> dict[str, Any]:
    """Copy public files and return the deterministic suite manifest."""
    source = source.resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"CORAL frontier_cs_algo directory not found: {source}")

    problem_dirs = _numeric_problem_dirs(source)
    if len(problem_dirs) != 172:
        raise ValueError(f"Expected 172 numeric problem directories, found {len(problem_dirs)}")

    PROBLEMS_DIR.mkdir(parents=True, exist_ok=True)
    manifest_problems: list[dict[str, Any]] = []

    for source_problem in problem_dirs:
        problem_id = source_problem.name
        statement_source = source_problem / "seed" / "statement.txt"
        initial_source = source_problem / "seed" / "solution.cpp"
        task_source = source_problem / "task.yaml"
        for required in (statement_source, initial_source, task_source):
            if not required.is_file():
                raise FileNotFoundError(f"Missing CORAL task file: {required}")

        task = _load_task(task_source)
        grader_args = dict(task.get("grader", {}).get("args", {}))
        yaml_problem_id = str(grader_args.get("problem_id", ""))
        if yaml_problem_id != problem_id:
            raise ValueError(
                f"Problem directory {problem_id} disagrees with task.yaml ID {yaml_problem_id}"
            )

        target_problem = PROBLEMS_DIR / problem_id
        target_problem.mkdir(parents=True, exist_ok=True)
        statement_target = target_problem / "statement.txt"
        initial_target = target_problem / "initial_program.cpp"
        shutil.copyfile(statement_source, statement_target)
        shutil.copyfile(initial_source, initial_target)

        task_name = str(task.get("task", {}).get("name", f"Frontier-CS Algo #{problem_id}"))
        title_prefix = f"Frontier-CS Algo #{problem_id}: "
        title = task_name.removeprefix(title_prefix)
        metadata = {
            "benchmark": "frontier_cs_algo",
            "track": "algorithmic",
            "problem_id": problem_id,
            "title": title,
            "problem_type": str(grader_args.get("problem_type", "default")),
            "time_limit_seconds": int(grader_args["time_limit"]),
            "memory_limit_mb": int(grader_args["memory_mb"]),
            "test_case_count": int(grader_args["n_cases"]),
            "source_task": f"examples/frontier_cs_algo/{problem_id}/task.yaml",
            "statement_sha256": _sha256(statement_target),
            "initial_program_sha256": _sha256(initial_target),
        }
        metadata_path = target_problem / "metadata.json"
        metadata_path.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        manifest_problems.append(metadata)

    imported_ids = {item["problem_id"] for item in manifest_problems}
    unexpected = sorted(
        (
            path.name
            for path in PROBLEMS_DIR.iterdir()
            if path.is_dir() and path.name.isdigit() and path.name not in imported_ids
        ),
        key=int,
    )
    if unexpected:
        raise ValueError(
            f"Target contains numeric problem directories absent from source: {unexpected}"
        )

    manifest = {
        "schema_version": 1,
        "benchmark": "frontier_cs_algo",
        "track": "algorithmic",
        "suite": "Frontier-CS v1 algorithmic track",
        "problem_count": len(manifest_problems),
        "source": {
            "repository": source_repository,
            "revision": source_revision,
            "path": "examples/frontier_cs_algo",
        },
        "judge_reference": {
            "repository": "https://github.com/FrontierCS/Frontier-CS.git",
            "tag": "v1.0.0",
            "revision": "c7ec558160646dbe797828ccd1a4fded503ab87c",
            "path": "algorithmic",
        },
        "problems": manifest_problems,
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Path to CORAL/examples/frontier_cs_algo",
    )
    parser.add_argument(
        "--source-repository",
        default="https://github.com/PsyAbyss/CORAL.git",
    )
    parser.add_argument("--source-revision", required=True)
    args = parser.parse_args()

    manifest = import_tasks(
        args.source,
        source_repository=args.source_repository,
        source_revision=args.source_revision,
    )
    print(f"Imported {manifest['problem_count']} problems into {PROBLEMS_DIR}")
    print(MANIFEST_PATH)


if __name__ == "__main__":
    main()

"""Filesystem helpers for controller-owned state.

The MCP server does not manage artifacts, logs, or the host agent's run
workspace.  It only owns a small SuccessDB and attempt log for each ``run_id``.
Artifact paths and log paths are treated as opaque references and are stored as
strings inside records.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List

DEFAULT_CONTROLLER_DIR = ".agent_evolve/controller"
ROOT_ENV_VAR = "AGENT_EVOLVE_CONTROLLER_DIR"


def controller_root() -> Path:
    """Return the root directory used for controller state.

    The default is intentionally local to the project.  Tests and host agents
    can override it with ``AGENT_EVOLVE_CONTROLLER_DIR``.
    """
    return Path(os.environ.get(ROOT_ENV_VAR, DEFAULT_CONTROLLER_DIR))


def run_dir(run_id: str) -> Path:
    """Return the state directory for one search run."""
    return controller_root() / _safe_name(run_id)


def success_db_path(run_id: str) -> Path:
    """Return the JSON file that stores successful reusable records."""
    return run_dir(run_id) / "success_db.json"


def attempts_log_path(run_id: str) -> Path:
    """Return the JSONL file that stores all final attempts."""
    return run_dir(run_id) / "attempts.jsonl"


def task_contexts_path(run_id: str) -> Path:
    """Return the JSON file that stores pre-record task selection context."""
    return run_dir(run_id) / "task_contexts.json"


def run_state_path(run_id: str) -> Path:
    """Return the JSON file that stores internal controller runtime state."""
    return run_dir(run_id) / "run_state.json"


def notes_path(run_id: str) -> Path:
    """Return the run-local Notes JSON file."""
    return run_dir(run_id) / "notes.json"


def note_requests_path(run_id: str) -> Path:
    """Return the append-only run-local note request log."""
    return run_dir(run_id) / "note_requests.jsonl"


def notes_events_path(run_id: str) -> Path:
    """Return the append-only run-local notes event log."""
    return run_dir(run_id) / "notes_events.jsonl"


def meta_guidance_dir(run_id: str) -> Path:
    """Return the directory for meta-guidance state."""
    return run_dir(run_id) / "meta_guidance"


def meta_guidance_state_path(run_id: str) -> Path:
    """Return the JSON file for meta-guidance lifecycle state."""
    return meta_guidance_dir(run_id) / "meta_guidance_state.json"


def meta_guidance_uses_path(run_id: str) -> Path:
    """Return the JSONL file for meta-guidance assignment/outcome events."""
    return meta_guidance_dir(run_id) / "meta_guidance_uses.jsonl"


def ensure_run_dir(run_id: str) -> Path:
    """Create and return the directory for one run."""
    path = run_dir(run_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    """Read a JSON object, returning ``default`` when the file is absent."""
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Read a JSONL file as a list of objects, skipping blank lines."""
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    """Write JSON with a replace step to avoid partially written state files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    tmp_path.replace(path)


def append_jsonl(path: Path, data: Dict[str, Any]) -> None:
    """Append one JSON object to a JSONL log."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(data, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def _safe_name(value: str) -> str:
    """Convert a caller-provided run id into a conservative path component."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe or "default"

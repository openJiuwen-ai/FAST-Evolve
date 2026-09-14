"""Every on-disk location EvoTrias owns, relative to the working directory.

Three roots::

    runs/<run_id>/          agent workspace; Python only reads here, except the
                            finalize report
    .evotrias/db/           all Python-side state, per run under runs/<run_id>/
    .evotrias/locks/        cross-process locks

Paths are relative on purpose: they resolve against the cwd when a file is
opened, so tests redirect everything with ``monkeypatch.chdir``.  This module
must stay free of ``evotrias`` imports -- ``evotrias.logger`` will read
``LOGS_LOCK`` from here while ``evotrias.db.base`` imports the logger.
"""

from __future__ import annotations

import re
from pathlib import Path

DB_ROOT = Path(".evotrias") / "db"
LOCKS_ROOT = Path(".evotrias") / "locks"
RUNS_ROOT = Path("runs")

SESSION_MAP = DB_ROOT / "session_map.json"
OPENCODE_REGISTRY = DB_ROOT / "opencode_registry.json"
HOOK_QUEUE_LOCK = LOCKS_ROOT / "hook_queue.lock"
LOGS_LOCK = LOCKS_ROOT / "logs.lock"

# Per-run files: join with run_dir() / public_run_dir().
RUN_STATE = "run_state.json"
TASK_CONTEXTS = "task_contexts.json"
SUCCESS_DB = "success_db.json"
ATTEMPTS = "attempts.jsonl"
NOTES = "notes.json"
NOTE_REQUESTS = "note_requests.jsonl"
NOTES_EVENTS = "notes_events.jsonl"
META_GUIDANCE_STATE = "meta_guidance_state.json"
META_GUIDANCE_USES = "meta_guidance_uses.jsonl"
TASK_POOL = "task_pool.json"
AGENT_INDEX = "agent_index.json"
RUN_SUMMARY_MD = "run_summary.md"
RUN_SUMMARY_JSON = "run_summary.json"


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe or "default"


def run_dir(run_id: str) -> Path:
    return DB_ROOT / "runs" / _safe_name(run_id)


def public_run_dir(run_id: str) -> Path:
    return RUNS_ROOT / _safe_name(run_id)

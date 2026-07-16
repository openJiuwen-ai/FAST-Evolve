"""Compact, MCP-owned persistence for host-agent scheduling state."""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict

_state_lock = threading.Lock()
_VALID_STATUSES = {"running", "completed"}


def initialize_scheduler_state(run_id: str) -> Dict[str, Any]:
    """Create an empty scheduler state without overwriting an existing run."""
    path = _state_path(run_id)
    with _state_lock:
        if not path.exists():
            _write_state(path, _empty_state(run_id))
        state = _read_state(path, run_id)
    return _compact_state(state)


def update_task_status(
    run_id: str,
    task_id: str,
    status: str,
    agent_id: str = "",
) -> Dict[str, Any]:
    """Apply one task transition and return only a compact acknowledgement."""
    normalized_status = str(status).strip().lower()
    if normalized_status not in _VALID_STATUSES:
        raise ValueError("status must be 'running' or 'completed'")

    normalized_task_id = str(task_id).strip()
    if not normalized_task_id:
        raise ValueError("task_id must not be empty")

    path = _state_path(run_id)
    with _state_lock:
        state = _read_state(path, run_id)
        running_tasks = [str(item) for item in state.get("running_tasks") or []]
        task_agents = state.get("task_agents")
        if not isinstance(task_agents, dict):
            task_agents = {}

        existing = task_agents.get(normalized_task_id)
        if not isinstance(existing, dict):
            existing = {}
        normalized_agent_id = str(agent_id).strip() or str(existing.get("agent_id") or "")

        if normalized_status == "running":
            if normalized_task_id not in running_tasks:
                running_tasks.append(normalized_task_id)
        else:
            running_tasks = [item for item in running_tasks if item != normalized_task_id]

        task_agents[normalized_task_id] = {
            "agent_id": normalized_agent_id,
            "status": normalized_status,
        }
        state = {
            "run_id": str(run_id),
            "running_tasks": running_tasks,
            "task_agents": task_agents,
        }
        _write_state(path, state)

    return {
        "updated": True,
        "task_id": normalized_task_id,
        "status": normalized_status,
        "running_count": len(running_tasks),
    }


def get_scheduler_state(run_id: str) -> Dict[str, Any]:
    """Return active scheduling state without historical completed mappings."""
    path = _state_path(run_id)
    with _state_lock:
        state = _read_state(path, run_id)
    return _compact_state(state)


def _compact_state(state: Dict[str, Any]) -> Dict[str, Any]:
    running_tasks = [str(item) for item in state.get("running_tasks") or []]
    task_agents = state.get("task_agents")
    if not isinstance(task_agents, dict):
        task_agents = {}
    active_agents = {
        task_id: str((task_agents.get(task_id) or {}).get("agent_id") or "")
        for task_id in running_tasks
    }
    return {
        "run_id": str(state.get("run_id") or ""),
        "running_tasks": running_tasks,
        "active_agents": active_agents,
        "running_count": len(running_tasks),
    }


def _state_path(run_id: str) -> Path:
    safe_run_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(run_id).strip()) or "default"
    return Path("runs") / safe_run_id / "scheduler_state.json"


def _empty_state(run_id: str) -> Dict[str, Any]:
    return {"run_id": str(run_id), "running_tasks": [], "task_agents": {}}


def _read_state(path: Path, run_id: str) -> Dict[str, Any]:
    if not path.exists():
        return _empty_state(run_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_state(run_id)
    return data if isinstance(data, dict) else _empty_state(run_id)


def _write_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temp_path.write_text(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(temp_path, path)

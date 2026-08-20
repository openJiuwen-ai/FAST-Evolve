from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from evotrias.enums import Agents
from evotrias.logger import logger

_STATE_LOCK = threading.Lock()
_SESSION_LOCK = threading.Lock()
_AGENT_LOCK = threading.Lock()

_STATE_DIR = Path(".evotrias") / "state"


def _pool_path(run_id: str) -> Path:
    return _STATE_DIR / "runs" / run_id / "task_pool.json"


def _session_map_path() -> Path:
    return _STATE_DIR / "session_run_map.json"


def _agent_index_path(run_id: str) -> Path:
    return _STATE_DIR / "runs" / run_id / "agent_index.json"


def _empty_pool(run_id: str) -> dict[str, Any]:
    return {
        "run_id": str(run_id),
        "created": [],
        "pending": [],
        "queued": {},
        "running": {},
        "completed": {},
        "failed": {},
        "next_seq": 1,
    }


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    return data if isinstance(data, dict) else default


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def remember_session_run(session_id: str, run_id: str) -> None:
    session_id = str(session_id).strip()
    run_id = str(run_id).strip()
    if not session_id or not run_id:
        logger.error(f'remember_session_run skipped: empty key. {session_id=!r}, {run_id=!r}')
        return
    with _SESSION_LOCK:
        data = _read_json(_session_map_path(), {})
        data[session_id] = run_id
        _write_json(_session_map_path(), data)
    logger.info(f'Session {session_id} mapped to run {run_id}')


def lookup_run_by_session(session_id: str) -> str | None:
    with _SESSION_LOCK:
        data = _read_json(_session_map_path(), {})
    result = data.get(str(session_id).strip())
    logger.debug(f'lookup_run_by_session: {session_id=!r} -> {result!r}')
    return str(result) if result else None


def remember_agent(run_id: str, agent_id: str, agent_info: dict[str, Any]) -> None:
    agent_info = dict(agent_info)  # copy to avoid mutating caller's dict
    agent_info["agent_type"] = agent_info["agent_type"].value
    with _AGENT_LOCK:
        data = _read_json(_agent_index_path(run_id), {})
        data[agent_id] = agent_info
        _write_json(_agent_index_path(run_id), data)
    logger.info(f'Agent {agent_id} indexed for run {run_id}')
    logger.debug(f'{agent_info=!r}')


def lookup_agent(run_id: str, agent_id: str) -> dict[str, Any] | None:
    with _AGENT_LOCK:
        data = _read_json(_agent_index_path(run_id), {})
    info = data.get(agent_id)
    if not info:
        logger.debug(f'lookup_agent: {agent_id=!r} not found in run {run_id}')
        return None
    info["agent_type"] = Agents(info["agent_type"])
    return info


def _load_pool(run_id: str) -> dict[str, Any]:
    return _read_json(_pool_path(run_id), _empty_pool(run_id))


def _save_pool(run_id: str, pool: dict[str, Any]) -> None:
    _write_json(_pool_path(run_id), pool)


def _next_task_id(pool: dict[str, Any]) -> str:
    seq = int(pool["next_seq"])
    pool["next_seq"] = seq + 1
    return f"task_{seq:03d}"


def top_up_tasks(run_id: str, target: int, max_tasks: int) -> list[str]:
    if target < 0:
        target = 0
    created: list[str] = []
    with _STATE_LOCK:
        pool = _load_pool(run_id)
        current = len(pool["created"]) + len(pool["pending"]) \
            + len(pool["queued"]) + len(pool["running"])
        already = int(pool["next_seq"]) - 1
        budget_target = current + max(max_tasks - already, 0)
        if budget_target < target:
            logger.info(
                f'top_up_tasks: run {run_id} capped by task budget '
                f'({already}/{max_tasks} created), target {target} -> {budget_target}'
            )
            target = budget_target
        while current < target:
            task_id = _next_task_id(pool)
            pool["created"].append(task_id)
            current += 1
            created.append(task_id)
        if created:
            _save_pool(run_id, pool)
    logger.info(f'top_up_tasks: created {len(created)} tasks for run {run_id}: {created}')
    return created


def allocate_task(run_id: str) -> str | None:
    with _STATE_LOCK:
        pool = _load_pool(run_id)
        if not pool["created"]:
            logger.error(f'allocate_task: no created tasks in run {run_id}')
            return None
        task_id = pool["created"].pop(0)
        pool["pending"].append(task_id)
        _save_pool(run_id, pool)
    logger.info(f'allocate_task: task {task_id} allocated in run {run_id}')
    return task_id


def queue_task(run_id: str, task_id: str, agent_id: str) -> None:
    with _STATE_LOCK:
        pool = _load_pool(run_id)
        was_pending = task_id in pool["pending"]
        if was_pending:
            pool["pending"].remove(task_id)
        pool["queued"][task_id] = str(agent_id)
        _save_pool(run_id, pool)
    if not was_pending:
        logger.critical(f'queue_task: task {task_id} was not in pending state in run {run_id}')
    logger.info(f'queue_task: task {task_id} queued for agent {agent_id} in run {run_id}')


def start_task(run_id: str, agent_id: str) -> str | None:
    agent_id = str(agent_id)
    with _STATE_LOCK:
        pool = _load_pool(run_id)
        for task_id, aid in pool["queued"].items():
            if aid == agent_id:
                pool["queued"].pop(task_id)
                pool["running"][task_id] = agent_id
                _save_pool(run_id, pool)
                logger.info(f'start_task: task {task_id} started by agent {agent_id} in run {run_id}')
                return task_id
    # Legitimate when SubagentStart fires before the spawner's PostToolUse
    # (parallel processes): the binding hasn't landed in queued yet.
    logger.info(f'start_task: no queued task yet for agent {agent_id} in run {run_id}')
    return None


def complete_task(run_id: str, task_id: str, fitness: Any) -> None:
    with _STATE_LOCK:
        pool = _load_pool(run_id)
        was_running = pool["running"].pop(task_id, None)
        pool["completed"][task_id] = {"fitness": fitness}
        _save_pool(run_id, pool)
    if was_running is None:
        logger.error(f'complete_task: task {task_id} was not in running state in run {run_id}')
    logger.info(f'complete_task: task {task_id} completed in run {run_id}, {fitness=!r}')


def fail_task(run_id: str, task_id: str, reason: str) -> None:
    with _STATE_LOCK:
        pool = _load_pool(run_id)
        was_running = pool["running"].pop(task_id, None)
        pool["failed"][task_id] = {"reason": str(reason)[:500]}
        _save_pool(run_id, pool)
    if was_running is None:
        logger.error(f'fail_task: task {task_id} was not in running state in run {run_id}')
    logger.info(f'fail_task: task {task_id} failed in run {run_id}, reason={str(reason)[:120]!r}')


def running_count(run_id: str) -> int:
    pool = _load_pool(run_id)
    return len(pool.get("running") or {})


def pool_snapshot(run_id: str) -> dict[str, Any]:
    pool = _load_pool(run_id)
    return {
        "run_id": run_id,
        "created": list(pool.get("created") or []),
        "pending": list(pool.get("pending") or []),
        "queued": dict(pool.get("queued") or {}),
        "running": dict(pool.get("running") or {}),
        "completed": dict(pool.get("completed") or {}),
        "failed": dict(pool.get("failed") or {}),
    }

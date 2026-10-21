# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Live, read-only dashboard for an evotrias run.

The dashboard reads run state through ``evotrias.db`` -- the same objects the
controller and scheduler write -- and never writes anything, so a UI failure
cannot affect an evolution run.  Paths are cwd-relative like everywhere else;
the working directory is the workspace.
"""

from __future__ import annotations

import enum
import json
import math
import os
import re
import threading
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from evotrias.db import paths
from evotrias.db.attempts import AttemptRecord, Attempts
from evotrias.db.config import Config
from evotrias.db.meta_guidance_events import MetaGuidanceEvent, MetaGuidanceEvents
from evotrias.db.meta_guidance_state import Assignment, MetaGuidanceState
from evotrias.db.notes import Note, Notes
from evotrias.db.notes_events import NotesEvent, NotesEvents
from evotrias.db.run_state import RunState
from evotrias.db.task_contexts import TaskContext, TaskContexts
from evotrias.db.task_pool import TaskAssigned, TaskPool
from evotrias.logger import logger

DEFAULT_DASHBOARD_HOST = "127.0.0.1"
DEFAULT_DASHBOARD_PORT = 4000
DASHBOARD_PORT_SEARCH_LIMIT = 100
UI_ENABLED_ENV = "EVOTRIAS_UI_ENABLED"
UI_HOST_ENV = "EVOTRIAS_UI_HOST"
UI_PUBLIC_HOST_ENV = "EVOTRIAS_UI_PUBLIC_HOST"
FALSE_ENV_VALUES = {"0", "false", "off", "no", "disabled"}


class DashboardServer:
    """One background HTTP server bound to one evolution run."""

    def __init__(
        self,
        *,
        run_id: str,
        host: str = DEFAULT_DASHBOARD_HOST,
        public_host: str | None = None,
        preferred_port: int = DEFAULT_DASHBOARD_PORT,
    ) -> None:
        self.run_id = run_id
        self.host = host
        self.public_host = public_host or host
        self._httpd = _bind_server(run_id=run_id, host=host, preferred_port=preferred_port)
        self.port = int(self._httpd.server_address[1])
        self.url = f"http://{_format_url_host(self.public_host)}:{self.port}"
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name=f"agent-evolve-ui-{run_id}",
            daemon=True,
        )

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def start(self) -> DashboardServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread.is_alive():
            self._thread.join(timeout=2)


_servers: dict[str, DashboardServer] = {}
_servers_lock = threading.Lock()


def dashboard_enabled() -> bool:
    """Return whether automatic dashboard startup is enabled."""
    value = os.environ.get(UI_ENABLED_ENV)
    return value is None or value.strip().lower() not in FALSE_ENV_VALUES


def start_dashboard(
    run_id: str,
    *,
    host: str | None = None,
    public_host: str | None = None,
    preferred_port: int = DEFAULT_DASHBOARD_PORT,
) -> DashboardServer | None:
    """Start or return the dashboard for ``run_id``.

    Binding the socket is the availability check.  This avoids a race between
    probing a port and starting the server, including when several MCP
    processes begin runs at nearly the same time.
    """
    if not dashboard_enabled():
        return None

    bind_host = _configured_host(host, UI_HOST_ENV, DEFAULT_DASHBOARD_HOST)
    advertised_host = _configured_host(public_host, UI_PUBLIC_HOST_ENV, bind_host)
    with _servers_lock:
        existing = _servers.get(run_id)
        if existing is not None and existing.is_alive:
            return existing
        server = DashboardServer(
            run_id=run_id,
            host=bind_host,
            public_host=advertised_host,
            preferred_port=preferred_port,
        ).start()
        _servers[run_id] = server
    logger.info(f"run {run_id}: dashboard at {server.url}")
    return server


def build_snapshot(run_id: str) -> dict[str, Any]:
    """Build a compact, JSON-safe view of all user-facing run state."""
    run_state = RunState(run_id)
    config = Config(run_id)
    pool = TaskPool(run_id)
    attempts = list(Attempts(run_id))
    completed = list(Attempts(run_id).final_by_task.values())
    notes = sorted(Notes(run_id), key=lambda note: note.created_at, reverse=True)
    note_events = list(NotesEvents(run_id))
    meta_state = MetaGuidanceState(run_id)
    meta_events = list(MetaGuidanceEvents(run_id))
    task_contexts = TaskContexts(run_id).tasks

    attempt_index = _attempt_index(attempts)
    progress = _build_progress(completed, attempt_index)
    best = _best_record(progress, attempt_index)
    running_tasks = list(pool.running)
    agents = _build_agents(
        public_run_dir=paths.public_run_dir(run_id),
        running=pool.running,
        attempts=attempts,
        task_contexts=task_contexts,
        assignments=meta_state.task_assignments,
    )
    lineage = _build_lineage(
        attempts=attempts,
        completed=completed,
        running_tasks=running_tasks,
        task_contexts=task_contexts,
    )
    guidance = _build_guidance(meta_state)
    activity = _build_activity(
        completed=completed,
        attempt_index=attempt_index,
        notes=notes,
        note_events=note_events,
        meta_events=meta_events,
        meta_state=meta_state,
        pool=pool,
        agents=agents,
    )

    diagnostics = dict(run_state.diagnostics or {})
    success_count = sum(1 for row in completed if row.in_success_pool)
    failed_count = len(completed) - success_count
    max_total_tasks = config.budget.max_total_tasks if config.created_at else None
    if max_total_tasks is None:
        max_total_tasks = _coerce_int(diagnostics.get("max_total_tasks"))
    started_at = run_state.created_at or None
    updated_at = run_state.updated_at or _latest_timestamp([row.created_at for row in attempts])
    search_state = run_state.search_state.value if run_state.created_at else "initializing"
    if search_state == "initializing" and (completed or running_tasks):
        search_state = "running"

    return {
        "generated_at": _now_iso(),
        "run": {
            "id": run_id,
            "state": search_state,
            "task_description": run_state.task_description,
            "max_total_tasks": max_total_tasks,
            "completed_tasks": len(completed),
            "successful_tasks": success_count,
            "failed_tasks": failed_count,
            "active_agents": len(agents),
            "started_at": started_at,
            "updated_at": updated_at,
            "elapsed_seconds": _elapsed_seconds(started_at, search_state),
            "stop_reason": run_state.stop_reason or diagnostics.get("stop_reason"),
        },
        "progress": progress,
        "best": best,
        "agents": agents,
        "notes": [_note_row(note) for note in notes[:40]],
        "guidance": guidance,
        "activity": activity[:60],
        "lineage": lineage,
        "features": {
            "meta_guidance": config.meta_evolve.enabled if config.created_at else None,
            "notes": config.notes.enabled if config.created_at else None,
        },
        "diagnostics": {
            "search_state": diagnostics.get("state") or search_state,
            "plateau_count": diagnostics.get("plateau_count"),
            "patience": diagnostics.get("patience"),
            "diversity": diagnostics.get("current_diversity"),
            "fitness_improvement": diagnostics.get("fitness_improvement"),
            "meta_guidance": diagnostics.get("meta_guidance") or {},
        },
    }


def list_runs(current_run_id: str) -> dict[str, Any]:
    """Return a flat, newest-first index of persisted runs.

    A run exists once the controller has state for it under
    ``.evotrias/db/runs/<run_id>/``; the agent workspace under ``runs/`` is
    not consulted.  Keeping this index flat lets one read-only dashboard
    inspect history without mutating the active controller.
    """
    runs_root = paths.DB_ROOT / "runs"
    entries: list[dict[str, Any]] = []
    if not runs_root.is_dir():
        return {"current": current_run_id, "runs": entries}

    for run_dir in runs_root.iterdir():
        if not run_dir.is_dir() or run_dir.is_symlink() or run_dir.name.startswith("."):
            continue
        run_id = run_dir.name
        pool = TaskPool(run_id)
        run_state = RunState(run_id)
        attempts = Attempts(run_id)
        completed = list(attempts.final_by_task.values())
        successful_fitness = [
            value
            for row in completed
            if row.in_success_pool
            for value in [_finite_float(row.verification_result.get("fitness"))]
            if value is not None
        ]
        name_match = re.match(r"^(?P<label>.+)_(?P<timestamp>\d{14})$", run_id)
        try:
            modified_at = datetime.fromtimestamp(run_dir.stat().st_mtime, UTC).isoformat()
        except OSError:
            modified_at = None
        updated_at = _latest_timestamp([run_state.updated_at, run_state.created_at, modified_at])
        updated_time = _parse_time(updated_at)
        recently_updated = bool(updated_time and (_utc_now() - updated_time).total_seconds() <= 15 * 60)
        entries.append(
            {
                "id": run_id,
                "label": name_match.group("label") if name_match else run_id,
                "timestamp": name_match.group("timestamp") if name_match else None,
                "status": "running" if pool.running and recently_updated else "stopped",
                "completed_tasks": len(completed),
                "attempts": len(attempts),
                "best_fitness": max(successful_fitness) if successful_fitness else None,
                "started_at": run_state.created_at or None,
                "updated_at": updated_at,
                "task_description": run_state.task_description,
                "is_launch_run": run_id == current_run_id,
            }
        )

    entries.sort(
        key=lambda item: (
            str(item.get("timestamp") or ""),
            str(item.get("updated_at") or ""),
            str(item["id"]),
        ),
        reverse=True,
    )
    return {"current": current_run_id, "runs": entries}


class _DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def _bind_server(*, run_id: str, host: str, preferred_port: int) -> _DashboardHTTPServer:
    handler = _handler_factory(run_id)
    if preferred_port == 0:
        return _DashboardHTTPServer((host, 0), handler)
    last_error: OSError | None = None
    for port in range(preferred_port, preferred_port + DASHBOARD_PORT_SEARCH_LIMIT):
        try:
            return _DashboardHTTPServer((host, port), handler)
        except OSError as exc:
            last_error = exc
            logger.debug(f"run {run_id}: dashboard port {port} busy, trying next")
    raise RuntimeError(
        f"No dashboard port available on {host} in "
        f"{preferred_port}-{preferred_port + DASHBOARD_PORT_SEARCH_LIMIT - 1}"
    ) from last_error


def _handler_factory(run_id: str) -> type[BaseHTTPRequestHandler]:
    static_root = Path(__file__).with_name("static")

    class Handler(BaseHTTPRequestHandler):
        server_version = "EvoTriasDashboard/1.0"

        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path == "/api/health":
                self._json({"status": "ok", "run_id": run_id})
                return
            if path == "/api/runs":
                self._json(list_runs(run_id))
                return
            if path == "/api/snapshot":
                requested_run_id = str(query.get("run_id", [run_id])[0])
                selected_run_id = _available_run_id(requested_run_id)
                if selected_run_id is None:
                    logger.warning(f"dashboard: snapshot requested for unknown run {requested_run_id!r}")
                    self._json(
                        {"error": "run not found", "run_id": requested_run_id},
                        status=HTTPStatus.NOT_FOUND,
                    )
                    return
                self._json(build_snapshot(selected_run_id))
                return
            asset_map = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/index.html": ("index.html", "text/html; charset=utf-8"),
                "/assets/styles.css": ("styles.css", "text/css; charset=utf-8"),
                "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/assets/evotrias-logo.png": ("evotrias-logo.png", "image/png"),
            }
            asset = asset_map.get(path)
            if asset is None:
                logger.warning(f"dashboard: 404 {path}")
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            file_path = static_root / asset[0]
            if not file_path.exists():
                logger.warning(f"dashboard: asset missing {file_path}")
                self.send_error(HTTPStatus.NOT_FOUND, "Dashboard asset missing")
                return
            payload = file_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", asset[1])
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

        def _json(self, data: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            payload = json.dumps(
                _json_safe(data),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            # Dashboard polling should not add noise to the MCP/agent terminal.
            return

    return Handler


def _build_lineage(
    *,
    attempts: list[AttemptRecord],
    completed: list[AttemptRecord],
    running_tasks: list[str],
    task_contexts: dict[str, TaskContext],
) -> dict[str, Any]:
    """Build a task-level lineage DAG for the read-only dashboard.

    Parent and inspiration selection are task-scoped, so the latest attempt
    represents a stable task node across retries. Active tasks are included
    before their first attempt so the graph advances in real time.
    """
    latest_by_task: dict[str, AttemptRecord] = {}
    for attempt in attempts:
        if attempt.task_id:
            latest_by_task[attempt.task_id] = attempt

    completed_by_task = {row.task_id: row for row in completed if row.task_id}
    running = set(running_tasks)
    task_ids = set(latest_by_task) | running
    nodes_by_id: dict[str, dict[str, Any]] = {}

    for task_id in task_ids:
        attempt = latest_by_task.get(task_id)
        context = task_contexts.get(task_id)
        completion = completed_by_task.get(task_id)
        verification = dict(attempt.verification_result or {}) if attempt else {}
        fitness = _finite_float(verification.get("fitness"))
        if fitness is None and completion is not None and completion.in_success_pool:
            fitness = _finite_float(completion.verification_result.get("fitness"))

        raw_status = str(verification.get("status") or "")
        if not raw_status and completion is not None:
            raw_status = "success" if completion.in_success_pool else "failure"
        status = "running" if task_id in running else raw_status.lower() or "pending"
        if status == "success" and fitness is None:
            status = "failure"

        if attempt is not None:
            parent_id = attempt.parent_id
            inspiration_ids = attempt.inspiration_ids
        else:
            parent_id = context.parent_id if context else None
            inspiration_ids = context.inspiration_ids if context else []

        nodes_by_id[task_id] = {
            "id": task_id,
            "task_id": task_id,
            "attempt_id": attempt.id if attempt else None,
            "parent_id": str(parent_id) if parent_id else None,
            "inspiration_ids": list(dict.fromkeys(str(item) for item in inspiration_ids if item)),
            "generation": attempt.generation if attempt else None,
            "fitness": fitness,
            "status": status,
            "created_at": attempt.created_at if attempt else None,
            "artifact_ref": attempt.artifact_ref if attempt else (completion.artifact_ref if completion else None),
            "meta_guidance_id": (attempt.meta_guidance_id if attempt else None)
            or (context.meta_guidance_id if context else None),
            "summary": str(
                (attempt.sketch if attempt else None)
                or verification.get("summary")
                or verification.get("feedback")
                or ""
            )[:240],
            "is_best": False,
            "is_new_best": False,
        }

    known_ids = set(nodes_by_id)
    for node in nodes_by_id.values():
        if node["parent_id"] not in known_ids:
            node["parent_id"] = None
        node["inspiration_ids"] = [
            item
            for item in node["inspiration_ids"]
            if item in known_ids and item != node["id"] and item != node["parent_id"]
        ]

    generation_cache: dict[str, int] = {}

    def generation_for(task_id: str, visiting: set[str] | None = None) -> int:
        if task_id in generation_cache:
            return generation_cache[task_id]
        node = nodes_by_id[task_id]
        explicit = node.get("generation")
        if explicit is not None and explicit >= 0:
            generation_cache[task_id] = explicit
            return explicit
        visiting = set(visiting or ())
        if task_id in visiting or not node.get("parent_id"):
            value = 0
        else:
            visiting.add(task_id)
            value = generation_for(str(node["parent_id"]), visiting) + 1
        generation_cache[task_id] = value
        return value

    def root_for(task_id: str) -> str:
        current = task_id
        visited: set[str] = set()
        while current not in visited:
            visited.add(current)
            parent_id = nodes_by_id[current].get("parent_id")
            if not parent_id or parent_id not in nodes_by_id:
                return current
            current = str(parent_id)
        return task_id

    for task_id, node in nodes_by_id.items():
        node["generation"] = generation_for(task_id)
        node["root_id"] = root_for(task_id)

    ordered_nodes = sorted(
        nodes_by_id.values(),
        key=lambda node: (
            int(node.get("generation") or 0),
            str(node.get("created_at") or ""),
            str(node["id"]),
        ),
    )
    best_fitness: float | None = None
    best_node: dict[str, Any] | None = None
    chronological_nodes = sorted(
        ordered_nodes,
        key=lambda node: (str(node.get("created_at") or ""), str(node["id"])),
    )
    for node in chronological_nodes:
        fitness = node.get("fitness")
        if node.get("status") != "success" or fitness is None:
            continue
        if best_fitness is None or fitness > best_fitness:
            best_fitness = fitness
            best_node = node
            node["is_new_best"] = True
    if best_node is not None:
        best_node["is_best"] = True

    parent_edges = [
        {"source": str(node["parent_id"]), "target": str(node["id"]), "kind": "parent"}
        for node in ordered_nodes
        if node.get("parent_id")
    ]
    inspiration_edges = [
        {"source": source, "target": str(node["id"]), "kind": "inspiration"}
        for node in ordered_nodes
        for source in node.get("inspiration_ids") or []
    ]
    roots = sorted({str(node["root_id"]) for node in ordered_nodes})
    return {
        "nodes": ordered_nodes,
        "parent_edges": parent_edges,
        "inspiration_edges": inspiration_edges,
        "roots": roots,
        "best_id": best_node.get("id") if best_node else None,
    }


def _build_progress(
    completed: list[AttemptRecord],
    attempt_index: dict[str, AttemptRecord],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    best_so_far: float | None = None
    for index, completion in enumerate(completed, start=1):
        attempt = attempt_index.get(completion.artifact_ref) or attempt_index.get(completion.task_id) or completion
        fitness = _finite_float(completion.verification_result.get("fitness")) if completion.in_success_pool else None
        status = "success" if completion.in_success_pool else "failure"
        is_new_best = False
        if status == "success" and fitness is not None:
            if best_so_far is None or fitness > best_so_far:
                best_so_far = fitness
                is_new_best = True
        rows.append(
            {
                "index": index,
                "task_id": completion.task_id,
                "attempt_id": attempt.id,
                "status": status,
                "fitness": fitness,
                "best_so_far": best_so_far,
                "is_new_best": is_new_best,
                "artifact_ref": completion.artifact_ref,
                "created_at": attempt.created_at,
                "meta_guidance_id": attempt.meta_guidance_id,
            }
        )
    return rows


def _best_record(
    progress: list[dict[str, Any]],
    attempt_index: dict[str, AttemptRecord],
) -> dict[str, Any] | None:
    candidates = [row for row in progress if row.get("fitness") is not None]
    if not candidates:
        return None
    winner = max(candidates, key=lambda row: float(row["fitness"]))
    attempt = attempt_index.get(str(winner.get("artifact_ref") or ""))
    verification = dict(attempt.verification_result or {}) if attempt else {}
    return {
        **winner,
        "metrics": verification.get("metrics") or {},
        "profile": verification.get("profile") or {},
        "generation": attempt.generation if attempt else None,
        "parent_id": attempt.parent_id if attempt else None,
        "inspiration_ids": list(attempt.inspiration_ids) if attempt else [],
    }


def _build_agents(
    *,
    public_run_dir: Path,
    running: dict[str, TaskAssigned],
    attempts: list[AttemptRecord],
    task_contexts: dict[str, TaskContext],
    assignments: dict[str, Assignment],
) -> list[dict[str, Any]]:
    latest_by_task: dict[str, AttemptRecord] = {}
    for attempt in attempts:
        if attempt.task_id:
            latest_by_task[attempt.task_id] = attempt

    agents: list[dict[str, Any]] = []
    for task_id, held in running.items():
        latest = latest_by_task.get(task_id)
        context = task_contexts.get(task_id)
        assignment = assignments.get(task_id)
        activity, detail = _infer_activity(public_run_dir / "tasks" / task_id, latest, context, assignment)
        agents.append(
            {
                "task_id": task_id,
                "agent_id": held.agent_id or (latest.subagent_id if latest else "") or "pending-id",
                "status": "active",
                "activity": activity,
                "detail": detail,
                "launched_at": held.at,
                "latest_attempt": latest.id if latest else None,
                "meta_guidance_id": assignment.guidance_id if assignment else None,
                "parent_id": context.parent_id if context else None,
                "inspiration_count": len(context.inspiration_ids) if context else 0,
            }
        )
    return agents


def _infer_activity(
    task_dir: Path,
    latest_attempt: AttemptRecord | None,
    context: TaskContext | None,
    assignment: Assignment | None,
) -> tuple[str, str]:
    verification = dict(latest_attempt.verification_result or {}) if latest_attempt else {}
    if str(verification.get("status") or "") == "failure":
        return "Debugging candidate", "A failed attempt was recorded; preparing the next fix."

    # The agent workspace under runs/<run_id>/ is the only thing the dashboard
    # reads outside db: whether the newest attempt dir has a candidate yet.
    attempts_dir = task_dir / "attempts"
    attempt_dirs = (
        sorted((path for path in attempts_dir.iterdir() if path.is_dir()), reverse=True)
        if attempts_dir.is_dir()
        else []
    )
    if attempt_dirs:
        newest = attempt_dirs[0]
        candidate_exists = any((newest / filename).exists() for filename in ("solution.py", "solution.cpp"))
        if candidate_exists and not (newest / "eval_result.json").exists():
            return "Verifying candidate", f"{newest.name} is ready for evaluation."
        if not candidate_exists:
            return "Generating candidate", f"Building {newest.name}."
    if assignment is not None:
        return "Applying meta-guidance", f"Implementing {assignment.guidance_id or 'meta-guidance'}."
    if context is not None and (context.parent_id is not None or context.inspiration_ids):
        return "Developing candidate", "Parent and inspiration context selected."
    if task_dir.exists():
        return "Exploring direction", "Preparing a new candidate approach."
    return "Selecting context", "Choosing parent, inspirations, and run-local knowledge."


def _build_guidance(meta_state: MetaGuidanceState) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for guidance_list, lifecycle in ((meta_state.active_guidance, "active"), (meta_state.tried_guidance, "tried")):
        for guidance in guidance_list:
            items.append(
                {
                    "guidance_id": guidance.guidance_id,
                    "idea": guidance.idea,
                    "description": guidance.description,
                    "created_at": guidance.created_at,
                    "best_fitness_at_creation": guidance.best_fitness_at_creation,
                    "lifecycle": lifecycle,
                    "uses": guidance.uses,
                    "max_uses": guidance.max_uses,
                    "status": guidance.status,
                    "outcome": guidance.outcome,
                    "anchor_parent_id": guidance.anchor_parent_id,
                    "approach_type": guidance.approach_type,
                }
            )
    pending = meta_state.pending_request
    if pending is not None:
        items.append(
            {
                "guidance_id": pending.request_id,
                "idea": "Meta-guidance generation requested",
                "description": "The controller detected a plateau and requested a new strategy.",
                "created_at": pending.created_at,
                "best_fitness_at_creation": pending.current_best.get("fitness"),
                "lifecycle": "pending",
                "uses": 0,
                "max_uses": None,
            }
        )
    return sorted(items, key=lambda item: str(item.get("created_at") or ""), reverse=True)


def _build_activity(
    *,
    completed: list[AttemptRecord],
    attempt_index: dict[str, AttemptRecord],
    notes: list[Note],
    note_events: list[NotesEvent],
    meta_events: list[MetaGuidanceEvent],
    meta_state: MetaGuidanceState,
    pool: TaskPool,
    agents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for agent in agents:
        events.append(
            {
                "kind": "agent",
                "title": f"{agent['task_id']} · {agent['activity']}",
                "detail": f"Agent {agent['agent_id']} — {agent['detail']}",
                "timestamp": agent["launched_at"] or _now_iso(),
                "live": True,
            }
        )
    # State-machine outcomes from the evotrias task pool.
    for task_id, done in pool.completed.items():
        attempt = attempt_index.get(task_id)
        events.append(
            {
                "kind": "task",
                "title": f"{task_id} completed",
                "detail": f"Fitness {_format_fitness(_finite_float(done.fitness))}",
                "timestamp": attempt.created_at if attempt else done.at,
                "status": "success",
            }
        )
    for task_id, failed in pool.failed.items():
        attempt = attempt_index.get(task_id)
        events.append(
            {
                "kind": "task",
                "title": f"{task_id} failed",
                "detail": failed.reason or "Task failed without a recorded reason.",
                "timestamp": attempt.created_at if attempt else failed.at,
                "status": "failed",
            }
        )
    # Per-task outcomes derived from attempts, deduped against the pool above.
    covered = set(pool.completed) | set(pool.failed)
    for row in completed[-30:]:
        if row.task_id in covered:
            continue
        fitness = _finite_float(row.verification_result.get("fitness")) if row.in_success_pool else None
        events.append(
            {
                "kind": "task",
                "title": f"{row.task_id} completed",
                "detail": (
                    f"Fitness {_format_fitness(fitness)}"
                    if row.in_success_pool
                    else "Task finished without a valid candidate"
                ),
                "timestamp": row.created_at,
                "status": "success" if row.in_success_pool else "failure",
            }
        )
    for note in notes[:15]:
        events.append(
            {
                "kind": "note",
                "title": note.title or "Run-local note recorded",
                "detail": note.lesson or note.actionable_guidance or "",
                "timestamp": note.created_at,
                "status": note.note_type,
            }
        )
    for event in note_events[-12:]:
        if event.event_type == "note_committed":
            continue
        events.append(
            {
                "kind": "system",
                "title": (event.event_type or "Notes event").replace("_", " ").title(),
                "detail": event.payload.get("reason") or "Notes policy updated.",
                "timestamp": event.created_at,
            }
        )
    for event in meta_events[-20:]:
        if event.event_type not in {"assigned", "recorded"}:
            continue
        payload = event.payload
        events.append(
            {
                "kind": "guidance",
                "title": (
                    f"{payload.get('guidance_id')} assigned to {payload.get('task_id')}"
                    if event.event_type == "assigned"
                    else f"{payload.get('guidance_id')} outcome recorded"
                ),
                "detail": (
                    f"Fitness {_format_fitness(_finite_float(payload.get('fitness')))}"
                    if event.event_type == "recorded"
                    else "A new strategy is being tested."
                ),
                "timestamp": event.created_at,
                "status": event.event_type,
            }
        )
    for guidance in meta_state.active_guidance[:8]:
        events.append(
            {
                "kind": "guidance",
                "title": guidance.idea or guidance.guidance_id,
                "detail": guidance.description or "Meta-guidance is active.",
                "timestamp": guidance.created_at,
                "status": "active",
            }
        )
    pending = meta_state.pending_request
    if pending is not None:
        events.append(
            {
                "kind": "guidance",
                "title": "Meta-guidance generation requested",
                "detail": "The controller detected a plateau and requested a new strategy.",
                "timestamp": pending.created_at,
                "status": "pending",
            }
        )
    return sorted(events, key=_activity_sort_key, reverse=True)


def _note_row(note: Note) -> dict[str, Any]:
    return {
        "note_id": note.note_id,
        "note_type": note.note_type,
        "title": note.title,
        "lesson": note.lesson,
        "actionable_guidance": note.actionable_guidance,
        "evidence_summary": note.evidence_summary,
        "confidence": note.confidence,
        "created_at": note.created_at,
    }


def _attempt_index(attempts: Iterable[AttemptRecord]) -> dict[str, AttemptRecord]:
    index: dict[str, AttemptRecord] = {}
    for attempt in attempts:
        if attempt.artifact_ref:
            index[attempt.artifact_ref] = attempt
        if attempt.task_id:
            index[attempt.task_id] = attempt
    return index


def _available_run_id(run_id: str) -> str | None:
    """Resolve a run that has controller state, without allowing path traversal."""
    requested = str(run_id).strip()
    safe = paths._safe_name(requested)
    if not requested or safe != requested:
        return None
    run_dir = paths.run_dir(safe)
    if not run_dir.is_dir() or run_dir.is_symlink():
        return None
    return safe


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _elapsed_seconds(started_at: Any, state: str) -> float | None:
    started = _parse_time(started_at)
    if started is None:
        return None
    if state == "stopped":
        return None
    return max((_utc_now() - started).total_seconds(), 0.0)


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _activity_sort_key(event: dict[str, Any]) -> datetime:
    return _parse_time(event.get("timestamp")) or datetime.min.replace(tzinfo=UTC)


def _latest_timestamp(values: Iterable[Any]) -> str | None:
    parsed = [(value, _parse_time(value)) for value in values]
    valid = [(value, timestamp) for value, timestamp in parsed if timestamp is not None]
    if not valid:
        return None
    return str(max(valid, key=lambda pair: pair[1])[0])


def _format_fitness(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.6g}"


def _json_safe(value: Any) -> Any:
    """Replace non-finite numbers with null and enums with their values."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _configured_host(value: str | None, env_name: str, default: str) -> str:
    configured = value if value is not None else os.environ.get(env_name)
    return str(configured or default).strip() or default


def _format_url_host(host: str) -> str:
    """Format an IPv6 host for a URL while leaving names and IPv4 unchanged."""
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _now_iso() -> str:
    return _utc_now().isoformat()


def main() -> None:
    """Serve a persisted run for local inspection or dashboard development."""
    import argparse

    parser = argparse.ArgumentParser(description="Serve an EvoTrias run dashboard.")
    parser.add_argument("run_id", help="Run id under .evotrias/db/runs/ (relative to cwd).")
    parser.add_argument(
        "--host",
        default=None,
        help=f"Bind address (default: ${UI_HOST_ENV} or {DEFAULT_DASHBOARD_HOST}).",
    )
    parser.add_argument(
        "--public-host",
        default=None,
        help=f"Public URL host (default: ${UI_PUBLIC_HOST_ENV} or bind address).",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_DASHBOARD_PORT)
    args = parser.parse_args()
    server = start_dashboard(
        args.run_id,
        host=args.host,
        public_host=args.public_host,
        preferred_port=args.port,
    )
    if server is None:
        raise SystemExit("Dashboard startup is disabled by EVOTRIAS_UI_ENABLED.")
    print(f"EvoTrias UI: {server.url}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()

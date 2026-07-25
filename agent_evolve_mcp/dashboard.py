"""Live, read-only dashboard for an Agent Evolve run.

The dashboard deliberately reads the same filesystem artifacts used by the
controller and scheduler.  It does not maintain a second database and it never
participates in scheduling, so a UI failure cannot affect an evolution run.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlparse

from agent_evolve_mcp.storage import controller_root

DEFAULT_DASHBOARD_HOST = "127.0.0.1"
DEFAULT_DASHBOARD_PORT = 4000
DASHBOARD_PORT_SEARCH_LIMIT = 100
UI_ENABLED_ENV = "AGENT_EVOLVE_UI_ENABLED"
FALSE_ENV_VALUES = {"0", "false", "off", "no", "disabled"}


class DashboardServer:
    """One background HTTP server bound to one evolution run."""

    def __init__(
        self,
        *,
        run_id: str,
        workspace_root: Path,
        host: str = DEFAULT_DASHBOARD_HOST,
        preferred_port: int = DEFAULT_DASHBOARD_PORT,
    ) -> None:
        self.run_id = run_id
        self.workspace_root = Path(workspace_root).resolve()
        self.host = host
        self._httpd = _bind_server(
            run_id=run_id,
            workspace_root=self.workspace_root,
            host=host,
            preferred_port=preferred_port,
        )
        self.port = int(self._httpd.server_address[1])
        self.url = f"http://{host}:{self.port}"
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name=f"agent-evolve-ui-{run_id}",
            daemon=True,
        )

    @property
    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def start(self) -> "DashboardServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread.is_alive():
            self._thread.join(timeout=2)


_servers: Dict[tuple[str, str], DashboardServer] = {}
_servers_lock = threading.Lock()


def dashboard_enabled() -> bool:
    """Return whether automatic dashboard startup is enabled."""
    value = os.environ.get(UI_ENABLED_ENV)
    return value is None or value.strip().lower() not in FALSE_ENV_VALUES


def start_dashboard(
    run_id: str,
    *,
    workspace_root: Optional[Path] = None,
    preferred_port: int = DEFAULT_DASHBOARD_PORT,
    max_total_tasks: Optional[int] = None,
    task_description: str = "",
    feature_flags: Optional[Dict[str, bool]] = None,
) -> Optional[DashboardServer]:
    """Start or return the dashboard for ``run_id``.

    Binding the socket is the availability check.  This avoids a race between
    probing a port and starting the server, including when several MCP
    processes begin runs at nearly the same time.
    """
    if not dashboard_enabled():
        return None

    root = Path(workspace_root or Path.cwd()).resolve()
    key = (str(root), run_id)
    with _servers_lock:
        existing = _servers.get(key)
        if existing is not None and existing.is_alive:
            return existing
        server = DashboardServer(
            run_id=run_id,
            workspace_root=root,
            preferred_port=preferred_port,
        ).start()
        _servers[key] = server

    _write_dashboard_manifest(
        root,
        run_id,
        url=server.url,
        port=server.port,
        max_total_tasks=max_total_tasks,
        task_description=task_description,
        feature_flags=feature_flags,
    )
    return server


def build_snapshot(run_id: str, workspace_root: Path) -> Dict[str, Any]:
    """Build a compact, JSON-safe view of all user-facing run state."""
    workspace_root = Path(workspace_root).resolve()
    safe_run_id = _safe_name(run_id)
    public_run_dir = workspace_root / "runs" / safe_run_id
    state_root = _controller_run_dir(workspace_root, safe_run_id)

    run_state = _read_json(state_root / "run_state.json", {})
    scheduler = _read_json(public_run_dir / "scheduler_state.json", {})
    manifest = _read_json(public_run_dir / "dashboard.json", {})
    completed = _read_jsonl(public_run_dir / "completed_tasks.jsonl")
    attempts = _read_jsonl(state_root / "attempts.jsonl")
    notes_collection = _read_json(state_root / "notes.json", {})
    notes_events = _read_jsonl(state_root / "notes_events.jsonl")
    meta_state = _read_json(state_root / "meta_guidance" / "meta_guidance_state.json", {})
    meta_uses = _read_jsonl(state_root / "meta_guidance" / "meta_guidance_uses.jsonl")

    attempt_index = _attempt_index(attempts)
    progress = _build_progress(completed, attempt_index)
    best = _best_record(progress, attempt_index)
    running_tasks = [str(item) for item in scheduler.get("running_tasks") or []]
    task_contexts = _read_json(state_root / "task_contexts.json", {})
    agents = _build_agents(
        public_run_dir=public_run_dir,
        running_tasks=running_tasks,
        task_agents=scheduler.get("task_agents") or {},
        attempts=attempts,
        task_contexts=task_contexts,
        meta_assignments=meta_state.get("task_assignments") or {},
    )
    lineage = _build_lineage(
        attempts=attempts,
        completed=completed,
        running_tasks=running_tasks,
        task_contexts=task_contexts,
    )

    notes = sorted(
        [dict(note) for note in notes_collection.get("notes") or []],
        key=lambda note: str(note.get("created_at") or ""),
        reverse=True,
    )
    guidance = _build_guidance(meta_state)
    feature_flags = dict(manifest.get("features") or {})
    activity = _build_activity(
        attempts=attempts,
        completed=completed,
        attempt_index=attempt_index,
        notes=notes,
        note_events=notes_events,
        meta_uses=meta_uses,
        guidance=guidance,
        agents=agents,
    )

    diagnostics = dict(run_state.get("diagnostics") or {})
    numeric_completed = len(completed)
    success_count = sum(1 for row in completed if row.get("status") == "success")
    failed_count = sum(1 for row in completed if row.get("status") != "success")
    max_total_tasks = _coerce_int(manifest.get("max_total_tasks"))
    if max_total_tasks is None:
        max_total_tasks = _coerce_int(diagnostics.get("max_total_tasks"))
    started_at = run_state.get("started_at") or manifest.get("started_at")
    updated_at = run_state.get("updated_at") or _latest_timestamp(
        [row.get("created_at") for row in attempts]
    )
    search_state = str(run_state.get("search_state") or "initializing")
    if search_state == "initializing" and (completed or running_tasks):
        search_state = "running"

    return {
        "generated_at": _now_iso(),
        "run": {
            "id": run_id,
            "state": search_state,
            "task_description": run_state.get("task_description")
            or manifest.get("task_description")
            or "",
            "max_total_tasks": max_total_tasks,
            "completed_tasks": numeric_completed,
            "successful_tasks": success_count,
            "failed_tasks": failed_count,
            "active_agents": len(agents),
            "started_at": started_at,
            "updated_at": updated_at,
            "elapsed_seconds": _elapsed_seconds(started_at, search_state),
            "stop_reason": run_state.get("stop_reason") or diagnostics.get("stop_reason"),
            "dashboard_url": manifest.get("url"),
        },
        "progress": progress,
        "best": best,
        "agents": agents,
        "notes": notes[:40],
        "guidance": guidance,
        "activity": activity[:60],
        "lineage": lineage,
        "features": {
            "meta_guidance": feature_flags.get("meta_guidance"),
            "notes": feature_flags.get("notes"),
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


def list_runs(current_run_id: str, workspace_root: Path) -> Dict[str, Any]:
    """Return a flat, newest-first index of persisted runs.

    Agent Evolve stores every run directly under ``runs/``.  Keeping this
    index flat mirrors that contract and lets one read-only dashboard inspect
    history without mutating the active controller or starting extra servers.
    """
    workspace_root = Path(workspace_root).resolve()
    runs_root = workspace_root / "runs"
    entries: List[Dict[str, Any]] = []
    if not runs_root.is_dir():
        return {"current": current_run_id, "runs": entries}

    for run_dir in runs_root.iterdir():
        if not run_dir.is_dir() or run_dir.is_symlink() or run_dir.name.startswith("."):
            continue
        run_id = run_dir.name
        state_root = _controller_run_dir(workspace_root, run_id)
        manifest = _read_json(run_dir / "dashboard.json", {})
        scheduler = _read_json(run_dir / "scheduler_state.json", {})
        run_state = _read_json(state_root / "run_state.json", {})
        completed = _read_jsonl(run_dir / "completed_tasks.jsonl")
        attempts = _read_jsonl(state_root / "attempts.jsonl")
        successful_fitness = [
            value
            for item in completed
            if item.get("status") == "success"
            for value in [_finite_float(item.get("fitness"))]
            if value is not None
        ]
        name_match = re.match(r"^(?P<label>.+)_(?P<timestamp>\d{14})$", run_id)
        try:
            modified_at = datetime.fromtimestamp(run_dir.stat().st_mtime, timezone.utc).isoformat()
        except OSError:
            modified_at = None
        updated_at = _latest_timestamp(
            [
                run_state.get("updated_at"),
                run_state.get("started_at"),
                manifest.get("started_at"),
                modified_at,
            ]
        )
        updated_time = _parse_time(updated_at)
        recently_updated = bool(
            updated_time and (_utc_now() - updated_time).total_seconds() <= 15 * 60
        )
        entries.append(
            {
                "id": run_id,
                "label": name_match.group("label") if name_match else run_id,
                "timestamp": name_match.group("timestamp") if name_match else None,
                "status": (
                    "running" if scheduler.get("running_tasks") and recently_updated else "stopped"
                ),
                "completed_tasks": len(completed),
                "attempts": len(attempts),
                "best_fitness": max(successful_fitness) if successful_fitness else None,
                "started_at": run_state.get("started_at") or manifest.get("started_at"),
                "updated_at": updated_at,
                "task_description": run_state.get("task_description")
                or manifest.get("task_description")
                or "",
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


def _bind_server(
    *,
    run_id: str,
    workspace_root: Path,
    host: str,
    preferred_port: int,
) -> _DashboardHTTPServer:
    handler = _handler_factory(run_id, workspace_root)
    if preferred_port == 0:
        return _DashboardHTTPServer((host, 0), handler)
    last_error: Optional[OSError] = None
    for port in range(preferred_port, preferred_port + DASHBOARD_PORT_SEARCH_LIMIT):
        try:
            return _DashboardHTTPServer((host, port), handler)
        except OSError as exc:
            last_error = exc
    raise RuntimeError(
        f"No dashboard port available on {host} in "
        f"{preferred_port}-{preferred_port + DASHBOARD_PORT_SEARCH_LIMIT - 1}"
    ) from last_error


def _handler_factory(run_id: str, workspace_root: Path) -> type[BaseHTTPRequestHandler]:
    static_root = Path(__file__).with_name("dashboard_static")

    class Handler(BaseHTTPRequestHandler):
        server_version = "AgentEvolveDashboard/1.0"

        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path == "/api/health":
                self._json({"status": "ok", "run_id": run_id})
                return
            if path == "/api/runs":
                self._json(list_runs(run_id, workspace_root))
                return
            if path == "/api/snapshot":
                requested_run_id = str(query.get("run_id", [run_id])[0])
                selected_run_id = _available_run_id(requested_run_id, workspace_root)
                if selected_run_id is None:
                    self._json(
                        {"error": "run not found", "run_id": requested_run_id},
                        status=HTTPStatus.NOT_FOUND,
                    )
                    return
                self._json(build_snapshot(selected_run_id, workspace_root))
                return
            asset_map = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/index.html": ("index.html", "text/html; charset=utf-8"),
                "/assets/styles.css": ("styles.css", "text/css; charset=utf-8"),
                "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/assets/evotrias-logo.png": ("evotrias-logo.png", "image/png"),
                "/assets/evotrias-logo-v5.png": ("evotrias-logo-v5.png", "image/png"),
            }
            asset = asset_map.get(path)
            if asset is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            file_path = static_root / asset[0]
            if not file_path.exists():
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

        def _json(self, data: Dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
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

        def log_message(self, _format: str, *_args: Any) -> None:
            # Dashboard polling should not add noise to the MCP/agent terminal.
            return

    return Handler


def _build_lineage(
    *,
    attempts: List[Dict[str, Any]],
    completed: List[Dict[str, Any]],
    running_tasks: List[str],
    task_contexts: Dict[str, Any],
) -> Dict[str, Any]:
    """Build a task-level lineage DAG for the read-only dashboard.

    Parent and inspiration selection are task-scoped, so the latest attempt
    represents a stable task node across retries. Active tasks are included
    before their first attempt so the graph advances in real time.
    """
    latest_by_task: Dict[str, Dict[str, Any]] = {}
    for attempt in attempts:
        task_id = str(attempt.get("task_id") or "")
        if task_id:
            latest_by_task[task_id] = attempt

    completed_by_task = {
        str(item.get("task_id") or ""): item for item in completed if item.get("task_id")
    }
    running = {str(task_id) for task_id in running_tasks}
    task_ids = set(latest_by_task) | running
    nodes_by_id: Dict[str, Dict[str, Any]] = {}

    for task_id in task_ids:
        attempt = latest_by_task.get(task_id) or {}
        context = task_contexts.get(task_id) or {}
        completion = completed_by_task.get(task_id) or {}
        verification = dict(attempt.get("verification_result") or {})
        fitness = _finite_float(verification.get("fitness"))
        if fitness is None:
            fitness = _finite_float(completion.get("fitness"))

        raw_status = str(verification.get("status") or completion.get("status") or "")
        status = "running" if task_id in running else raw_status.lower() or "pending"
        if status == "success" and fitness is None:
            status = "failure"

        parent_id = attempt.get("parent_id", context.get("parent_id"))
        inspiration_ids = attempt.get("inspiration_ids")
        if inspiration_ids is None:
            inspiration_ids = context.get("inspiration_ids") or []

        nodes_by_id[task_id] = {
            "id": task_id,
            "task_id": task_id,
            "attempt_id": attempt.get("id"),
            "parent_id": str(parent_id) if parent_id else None,
            "inspiration_ids": list(dict.fromkeys(str(item) for item in inspiration_ids if item)),
            "generation": _coerce_int(attempt.get("generation")),
            "fitness": fitness,
            "status": status,
            "created_at": attempt.get("created_at") or context.get("created_at"),
            "artifact_ref": attempt.get("artifact_ref") or completion.get("artifact_ref"),
            "meta_guidance_id": attempt.get("meta_guidance_id") or context.get("meta_guidance_id"),
            "summary": str(
                attempt.get("sketch")
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

    generation_cache: Dict[str, int] = {}

    def generation_for(task_id: str, visiting: Optional[set[str]] = None) -> int:
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
    best_fitness: Optional[float] = None
    best_node: Optional[Dict[str, Any]] = None
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
    completed: List[Dict[str, Any]],
    attempt_index: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    best_so_far: Optional[float] = None
    for index, completion in enumerate(completed, start=1):
        artifact_ref = str(completion.get("artifact_ref") or "")
        attempt = (
            attempt_index.get(artifact_ref)
            or attempt_index.get(str(completion.get("task_id") or ""))
            or {}
        )
        fitness = _finite_float(completion.get("fitness"))
        status = str(completion.get("status") or "unknown")
        is_new_best = False
        if status == "success" and fitness is not None:
            if best_so_far is None or fitness > best_so_far:
                best_so_far = fitness
                is_new_best = True
        rows.append(
            {
                "index": index,
                "task_id": str(completion.get("task_id") or ""),
                "attempt_id": attempt.get("id"),
                "status": status,
                "fitness": fitness,
                "best_so_far": best_so_far,
                "is_new_best": is_new_best,
                "artifact_ref": artifact_ref,
                "created_at": attempt.get("created_at"),
                "meta_guidance_id": attempt.get("meta_guidance_id"),
            }
        )
    return rows


def _best_record(
    progress: List[Dict[str, Any]],
    attempt_index: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    candidates = [row for row in progress if row.get("fitness") is not None]
    if not candidates:
        return None
    winner = max(candidates, key=lambda row: float(row["fitness"]))
    attempt = attempt_index.get(str(winner.get("artifact_ref") or "")) or {}
    verification = dict(attempt.get("verification_result") or {})
    return {
        **winner,
        "metrics": verification.get("metrics") or {},
        "profile": verification.get("profile") or {},
        "generation": attempt.get("generation"),
        "parent_id": attempt.get("parent_id"),
        "inspiration_ids": attempt.get("inspiration_ids") or [],
    }


def _build_agents(
    *,
    public_run_dir: Path,
    running_tasks: List[str],
    task_agents: Dict[str, Any],
    attempts: List[Dict[str, Any]],
    task_contexts: Dict[str, Any],
    meta_assignments: Dict[str, Any],
) -> List[Dict[str, Any]]:
    latest_by_task: Dict[str, Dict[str, Any]] = {}
    for attempt in attempts:
        task_id = str(attempt.get("task_id") or "")
        if task_id:
            latest_by_task[task_id] = attempt

    agents: List[Dict[str, Any]] = []
    for task_id in running_tasks:
        mapping = task_agents.get(task_id) or {}
        if isinstance(mapping, str):
            mapping = {"agent_id": mapping}
        latest = latest_by_task.get(task_id) or {}
        context = task_contexts.get(task_id) or {}
        guidance_assignment = meta_assignments.get(task_id) or {}
        activity, detail = _infer_activity(
            public_run_dir / "tasks" / task_id,
            latest,
            context,
            guidance_assignment,
        )
        agents.append(
            {
                "task_id": task_id,
                "agent_id": mapping.get("agent_id") or latest.get("subagent_id") or "pending-id",
                "status": "active",
                "activity": activity,
                "detail": detail,
                "launched_at": mapping.get("launched_at"),
                "latest_attempt": latest.get("id"),
                "meta_guidance_id": guidance_assignment.get("guidance_id"),
                "parent_id": context.get("parent_id"),
                "inspiration_count": len(context.get("inspiration_ids") or []),
            }
        )
    return agents


def _infer_activity(
    task_dir: Path,
    latest_attempt: Dict[str, Any],
    context: Dict[str, Any],
    guidance_assignment: Dict[str, Any],
) -> tuple[str, str]:
    verification = dict(latest_attempt.get("verification_result") or {})
    if str(verification.get("status") or "") == "failure":
        return "Debugging candidate", "A failed attempt was recorded; preparing the next fix."

    attempts_dir = task_dir / "attempts"
    attempt_dirs = (
        sorted((path for path in attempts_dir.iterdir() if path.is_dir()), reverse=True)
        if attempts_dir.is_dir()
        else []
    )
    if attempt_dirs:
        newest = attempt_dirs[0]
        candidate_exists = any(
            (newest / filename).exists() for filename in ("solution.py", "solution.cpp")
        )
        if candidate_exists and not (newest / "eval_result.json").exists():
            return "Verifying candidate", f"{newest.name} is ready for evaluation."
        if not candidate_exists:
            return "Generating candidate", f"Building {newest.name}."
    if guidance_assignment:
        guidance_id = guidance_assignment.get("guidance_id") or "meta-guidance"
        return "Applying meta-guidance", f"Implementing {guidance_id}."
    if context.get("parent_id") is not None or context.get("inspiration_ids"):
        return "Developing candidate", "Parent and inspiration context selected."
    if task_dir.exists():
        return "Exploring direction", "Preparing a new candidate approach."
    return "Selecting context", "Choosing parent, inspirations, and run-local knowledge."


def _build_guidance(meta_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for status_key, normalized_status in (
        ("active_guidance", "active"),
        ("tried_guidance", "tried"),
    ):
        for raw in meta_state.get(status_key) or []:
            item = dict(raw)
            item["lifecycle"] = normalized_status
            items.append(item)
    pending = meta_state.get("pending_request")
    if pending:
        current_best = dict(pending.get("current_best") or {})
        items.append(
            {
                "guidance_id": pending.get("request_id"),
                "idea": "Meta-guidance generation requested",
                "description": "The controller detected a plateau and requested a new strategy.",
                "created_at": pending.get("created_at"),
                "best_fitness_at_creation": current_best.get("fitness"),
                "lifecycle": "pending",
                "uses": 0,
                "max_uses": None,
            }
        )
    return sorted(
        items,
        key=lambda item: str(item.get("created_at") or ""),
        reverse=True,
    )


def _build_activity(
    *,
    attempts: List[Dict[str, Any]],
    completed: List[Dict[str, Any]],
    attempt_index: Dict[str, Dict[str, Any]],
    notes: List[Dict[str, Any]],
    note_events: List[Dict[str, Any]],
    meta_uses: List[Dict[str, Any]],
    guidance: List[Dict[str, Any]],
    agents: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for agent in agents:
        events.append(
            {
                "kind": "agent",
                "title": f"{agent['task_id']} · {agent['activity']}",
                "detail": f"Agent {agent['agent_id']} — {agent['detail']}",
                "timestamp": agent.get("launched_at") or _now_iso(),
                "live": True,
            }
        )
    for row in completed[-30:]:
        attempt = attempt_index.get(str(row.get("artifact_ref") or "")) or {}
        fitness = _finite_float(row.get("fitness"))
        events.append(
            {
                "kind": "task",
                "title": f"{row.get('task_id')} completed",
                "detail": (
                    f"Fitness {_format_fitness(fitness)}"
                    if row.get("status") == "success"
                    else "Task finished without a valid candidate"
                ),
                "timestamp": attempt.get("created_at"),
                "status": row.get("status"),
            }
        )
    for note in notes[:15]:
        events.append(
            {
                "kind": "note",
                "title": note.get("title") or "Run-local note recorded",
                "detail": note.get("lesson") or note.get("actionable_guidance") or "",
                "timestamp": note.get("created_at"),
                "status": note.get("note_type"),
            }
        )
    for event in note_events[-12:]:
        if event.get("event") == "note_committed":
            continue
        events.append(
            {
                "kind": "system",
                "title": str(event.get("event") or "Notes event").replace("_", " ").title(),
                "detail": event.get("reason") or "Notes policy updated.",
                "timestamp": event.get("created_at") or event.get("timestamp"),
            }
        )
    for event in meta_uses[-20:]:
        status = str(event.get("status") or "")
        if status not in {"assigned", "recorded"}:
            continue
        events.append(
            {
                "kind": "guidance",
                "title": (
                    f"{event.get('guidance_id')} assigned to {event.get('task_id')}"
                    if status == "assigned"
                    else f"{event.get('guidance_id')} outcome recorded"
                ),
                "detail": (
                    f"Fitness {_format_fitness(_finite_float(event.get('fitness')))}"
                    if status == "recorded"
                    else "A new strategy is being tested."
                ),
                "timestamp": event.get("assigned_at") or event.get("recorded_at"),
                "status": status,
            }
        )
    for item in guidance[:8]:
        if item.get("lifecycle") not in {"pending", "active"}:
            continue
        events.append(
            {
                "kind": "guidance",
                "title": item.get("idea") or item.get("guidance_id"),
                "detail": item.get("description") or "Meta-guidance is active.",
                "timestamp": item.get("created_at"),
                "status": item.get("lifecycle"),
            }
        )
    return sorted(events, key=_activity_sort_key, reverse=True)


def _attempt_index(attempts: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for attempt in attempts:
        artifact_ref = str(attempt.get("artifact_ref") or "")
        task_id = str(attempt.get("task_id") or "")
        if artifact_ref:
            index[artifact_ref] = attempt
        if task_id:
            index[task_id] = attempt
    return index


def _controller_run_dir(workspace_root: Path, run_id: str) -> Path:
    root = controller_root()
    if not root.is_absolute():
        root = workspace_root / root
    return root / run_id


def _available_run_id(run_id: str, workspace_root: Path) -> Optional[str]:
    """Resolve a direct child of ``runs/`` without allowing path traversal."""
    requested = str(run_id).strip()
    safe = _safe_name(requested)
    if not requested or safe != requested:
        return None
    run_dir = Path(workspace_root).resolve() / "runs" / safe
    if not run_dir.is_dir() or run_dir.is_symlink():
        return None
    return safe


def _write_dashboard_manifest(
    workspace_root: Path,
    run_id: str,
    *,
    url: str,
    port: int,
    max_total_tasks: Optional[int],
    task_description: str,
    feature_flags: Optional[Dict[str, bool]],
) -> None:
    run_path = workspace_root / "runs" / _safe_name(run_id)
    run_path.mkdir(parents=True, exist_ok=True)
    path = run_path / "dashboard.json"
    existing = _read_json(path, {})
    payload = {
        **existing,
        "run_id": run_id,
        "url": url,
        "port": port,
        "max_total_tasks": (
            max_total_tasks if max_total_tasks is not None else existing.get("max_total_tasks")
        ),
        "task_description": task_description or existing.get("task_description") or "",
        "features": dict(feature_flags or {}),
        "started_at": existing.get("started_at") or _now_iso(),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else default
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    # An append may be in flight while the UI polls.
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except (OSError, UnicodeDecodeError):
        pass
    return rows


def _finite_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _coerce_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _elapsed_seconds(started_at: Any, state: str) -> Optional[float]:
    started = _parse_time(started_at)
    if started is None:
        return None
    if state == "stopped":
        return None
    return max((_utc_now() - started).total_seconds(), 0.0)


def _parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _activity_sort_key(event: Dict[str, Any]) -> datetime:
    return _parse_time(event.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc)


def _latest_timestamp(values: Iterable[Any]) -> Optional[str]:
    parsed = [(value, _parse_time(value)) for value in values]
    valid = [(value, timestamp) for value, timestamp in parsed if timestamp is not None]
    if not valid:
        return None
    return str(max(valid, key=lambda pair: pair[1])[0])


def _format_fitness(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:.6g}"


def _json_safe(value: Any) -> Any:
    """Replace non-finite numbers in nested verifier metrics with null."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value).strip())
    return safe or "default"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _utc_now().isoformat()


def main() -> None:
    """Serve a persisted run for local inspection or dashboard development."""
    import argparse

    parser = argparse.ArgumentParser(description="Serve an EvoTrias run dashboard.")
    parser.add_argument("run_id", help="Run id under runs/ and controller state.")
    parser.add_argument("--port", type=int, default=DEFAULT_DASHBOARD_PORT)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    args = parser.parse_args()
    server = start_dashboard(
        args.run_id,
        workspace_root=args.workspace,
        preferred_port=args.port,
    )
    if server is None:
        raise SystemExit("Dashboard startup is disabled by AGENT_EVOLVE_UI_ENABLED.")
    print(f"EvoTrias UI: {server.url}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()

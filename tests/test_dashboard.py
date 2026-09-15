import json
import socket
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import pytest

from evotrias.db.config import Config
import evotrias.mcp.dashboard as dashboard_module
import evotrias.mcp.public_tools as public_tools_module
from evotrias.mcp.dashboard import DashboardServer, build_snapshot, list_runs

RUN_ID = "signal_processing_20260715010101"


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_jsonl(path: Path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(value) + "\n" for value in values),
        encoding="utf-8",
    )


def _seed_run(tmp_path: Path, monkeypatch):
    controller_root = tmp_path / ".agent_evolve" / "controller"
    monkeypatch.setenv("AGENT_EVOLVE_CONTROLLER_DIR", str(controller_root))
    public = tmp_path / "runs" / RUN_ID
    state = controller_root / RUN_ID

    _write_json(
        tmp_path / ".evotrias" / "db" / "runs" / RUN_ID / "config.json",
        {
            "budget": {"max_total_tasks": 50},
            "meta_evolve": {"enabled": True},
            "notes": {"enabled": False},
        },
    )
    _write_json(
        tmp_path / ".evotrias" / "state" / "runs" / RUN_ID / "task_pool.json",
        {
            "run_id": RUN_ID,
            "pending": [],
            "running": {"task_003": "agent-live-3"},
            "completed": {},
            "failed": {},
        },
    )
    completed = [
        {
            "task_id": "task_001",
            "fitness": 0.4,
            "status": "success",
            "artifact_ref": f"runs/{RUN_ID}/tasks/task_001/attempts/attempt_001/solution.py",
        },
        {
            "task_id": "task_002",
            "fitness": 0.7,
            "status": "success",
            "artifact_ref": f"runs/{RUN_ID}/tasks/task_002/attempts/attempt_001/solution.py",
        },
    ]
    _write_jsonl(public / "completed_tasks.jsonl", completed)
    attempts = []
    for index, row in enumerate(completed, start=1):
        attempts.append(
            {
                "id": "attempt_001",
                "task_id": row["task_id"],
                "artifact_ref": row["artifact_ref"],
                "created_at": f"2026-07-15T01:0{index}:00+00:00",
                "generation": index - 1,
                "parent_id": None if index == 1 else "task_001",
                "verification_result": {
                    "status": "success",
                    "fitness": row["fitness"],
                    "metrics": {"accuracy_score": row["fitness"], "bad": float("nan")},
                },
            }
        )
    _write_jsonl(state / "attempts.jsonl", attempts)
    _write_json(
        tmp_path / ".evotrias" / "db" / "runs" / RUN_ID / "run_state.json",
        {
            "search_state": "watching",
            "created_at": "2026-07-15T01:00:00+00:00",
            "updated_at": "2026-07-15T01:03:00+00:00",
            "task_description": "Improve the benchmark candidate.",
            "diagnostics": {
                "plateau_count": 3,
                "patience": 4,
                "current_diversity": 0.82,
            },
        },
    )
    _write_json(
        state / "task_contexts.json",
        {"task_003": {"parent_id": "task_002", "inspiration_ids": ["task_001"]}},
    )
    _write_json(
        state / "notes.json",
        {
            "notes": [
                {
                    "note_id": "note_positive_1",
                    "note_type": "positive",
                    "title": "Keep the stable update",
                    "lesson": "It improved fitness.",
                    "created_at": "2026-07-15T01:02:30+00:00",
                }
            ]
        },
    )
    _write_json(
        state / "meta_guidance" / "meta_guidance_state.json",
        {
            "active_guidance": [
                {
                    "guidance_id": "mg_0001",
                    "idea": "Try a new search family",
                    "description": "Escape the current plateau.",
                    "created_at": "2026-07-15T01:02:40+00:00",
                    "uses": 1,
                    "max_uses": 2,
                }
            ],
            "tried_guidance": [],
            "task_assignments": {"task_003": {"guidance_id": "mg_0001"}},
            "pending_request": None,
        },
    )
    return public, state


def _seed_history_run(tmp_path: Path):
    run_id = "run_20260714010101"
    public = tmp_path / "runs" / run_id
    state = tmp_path / ".agent_evolve" / "controller" / run_id
    artifact = f"runs/{run_id}/tasks/task_001/attempts/attempt_001/solution.py"
    _write_json(
        tmp_path / ".evotrias" / "state" / "runs" / run_id / "task_pool.json",
        {"run_id": run_id, "pending": [], "running": {}, "completed": {}, "failed": {}},
    )
    _write_jsonl(
        public / "completed_tasks.jsonl",
        [
            {
                "task_id": "task_001",
                "fitness": 0.2,
                "status": "success",
                "artifact_ref": artifact,
            }
        ],
    )
    _write_jsonl(
        state / "attempts.jsonl",
        [
            {
                "id": "attempt_001",
                "task_id": "task_001",
                "artifact_ref": artifact,
                "created_at": "2026-07-14T01:02:00+00:00",
                "generation": 0,
                "verification_result": {"status": "success", "fitness": 0.2},
            }
        ],
    )
    _write_json(
        tmp_path / ".evotrias" / "db" / "runs" / run_id / "run_state.json",
        {
            "search_state": "stopped",
            "task_description": "An older persisted run.",
            "created_at": "2026-07-14T01:01:01+00:00",
            "updated_at": "2026-07-14T01:03:00+00:00",
        },
    )
    return run_id


def test_build_snapshot_aggregates_live_run_state(tmp_path, monkeypatch):
    _seed_run(tmp_path, monkeypatch)

    snapshot = build_snapshot(RUN_ID, tmp_path)

    assert snapshot["run"]["completed_tasks"] == 2
    assert snapshot["run"]["max_total_tasks"] == 50
    assert snapshot["best"]["task_id"] == "task_002"
    assert snapshot["best"]["fitness"] == 0.7
    assert snapshot["progress"][0]["is_new_best"] is True
    assert snapshot["progress"][1]["best_so_far"] == 0.7
    assert snapshot["agents"][0]["agent_id"] == "agent-live-3"
    assert snapshot["agents"][0]["activity"] == "Applying meta-guidance"
    assert snapshot["notes"][0]["note_id"] == "note_positive_1"
    assert snapshot["guidance"][0]["guidance_id"] == "mg_0001"
    assert snapshot["features"] == {"meta_guidance": True, "notes": False}
    assert any(item["kind"] == "agent" for item in snapshot["activity"])
    assert [node["id"] for node in snapshot["lineage"]["nodes"]] == [
        "task_001",
        "task_002",
        "task_003",
    ]
    assert snapshot["lineage"]["best_id"] == "task_002"
    assert snapshot["lineage"]["parent_edges"] == [
        {"source": "task_001", "target": "task_002", "kind": "parent"},
        {"source": "task_002", "target": "task_003", "kind": "parent"},
    ]
    assert snapshot["lineage"]["inspiration_edges"] == [
        {"source": "task_001", "target": "task_003", "kind": "inspiration"}
    ]
    assert snapshot["lineage"]["nodes"][-1]["status"] == "running"


def test_infer_activity_recognizes_cpp_candidate(tmp_path):
    task_dir = tmp_path / "task_001"
    attempt_dir = task_dir / "attempts" / "attempt_001"
    attempt_dir.mkdir(parents=True)
    (attempt_dir / "solution.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")

    activity, detail = dashboard_module._infer_activity(task_dir, {}, {}, {})

    assert activity == "Verifying candidate"
    assert "attempt_001" in detail


def test_recent_activity_links_to_evotrias_state_machine(tmp_path, monkeypatch):
    """§5.4: Recent activity shows completed(fitness) / failed(reason) sourced
    from .evotrias/state/runs/<run_id>/task_pool.json, not just the legacy
    success log."""
    _seed_run(tmp_path, monkeypatch)

    # Seed the evotrias task pool with a completed task (fitness) and a failed
    # task (reason). The dashboard must surface both in Recent activity.
    pool_path = tmp_path / ".evotrias" / "state" / "runs" / RUN_ID / "task_pool.json"
    _write_json(
        pool_path,
        {
            "run_id": RUN_ID,
            "pending": [],
            "running": {},
            "completed": {"task_004": {"fitness": 0.91}},
            "failed": {"task_005": {"reason": "candidate did not compile"}},
        },
    )

    snapshot = build_snapshot(RUN_ID, tmp_path)
    activity = snapshot["activity"]
    titles = {item["title"] for item in activity}

    assert "task_004 completed" in titles
    completed_row = next(item for item in activity if item["title"] == "task_004 completed")
    assert completed_row["status"] == "success"
    assert "0.91" in completed_row["detail"]

    assert "task_005 failed" in titles
    failed_row = next(item for item in activity if item["title"] == "task_005 failed")
    assert failed_row["status"] == "failed"
    assert "did not compile" in failed_row["detail"]


def test_configure_run_freezes_feature_flags_into_config(tmp_path, monkeypatch):
    run_id = "feature_flags_20260722010101"
    captured = {}

    class FakeDashboardServer:
        url = "http://127.0.0.1:4567"
        port = 4567

    def fake_start_dashboard(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeDashboardServer()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "AGENT_EVOLVE_CONTROLLER_DIR", str(tmp_path / ".agent_evolve" / "controller")
    )
    monkeypatch.setenv("AGENT_EVOLVE_META_EVOLVE_ENABLED", "0")
    monkeypatch.setenv("AGENT_EVOLVE_NOTES_ENABLED", "0")
    monkeypatch.setattr(public_tools_module, "start_dashboard", fake_start_dashboard)
    public_tools_module._controllers.pop(run_id, None)

    try:
        result = public_tools_module.evolve_configure_run(run_id, max_total_tasks=20)
    finally:
        public_tools_module._controllers.pop(run_id, None)

    assert result["meta_evolve"]["enabled"] is False
    config = Config(run_id)
    assert config.meta_evolve.enabled is False
    assert config.notes.enabled is False
    # The dashboard reads flags from config.json itself; nothing is passed in.
    assert captured == {"args": (run_id,), "kwargs": {}}


def test_list_runs_returns_flat_history_for_named_and_legacy_ids(tmp_path, monkeypatch):
    _seed_run(tmp_path, monkeypatch)
    history_run_id = _seed_history_run(tmp_path)
    monkeypatch.setattr(
        dashboard_module,
        "_utc_now",
        lambda: datetime(2026, 7, 15, 1, 4, tzinfo=timezone.utc),
    )

    history = list_runs(RUN_ID, tmp_path)

    assert history["current"] == RUN_ID
    assert [item["id"] for item in history["runs"]] == [RUN_ID, history_run_id]
    assert history["runs"][0]["label"] == "signal_processing"
    assert history["runs"][0]["timestamp"] == "20260715010101"
    assert history["runs"][0]["status"] == "running"
    assert history["runs"][0]["completed_tasks"] == 2
    assert history["runs"][0]["best_fitness"] == 0.7
    assert history["runs"][1]["label"] == "run"
    assert history["runs"][1]["status"] == "stopped"


def test_dashboard_uses_next_port_and_serves_snapshot(tmp_path, monkeypatch):
    _seed_run(tmp_path, monkeypatch)
    history_run_id = _seed_history_run(tmp_path)
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    preferred = blocker.getsockname()[1]
    if preferred >= 65535:
        blocker.close()
        return

    server = DashboardServer(
        run_id=RUN_ID,
        workspace_root=tmp_path,
        preferred_port=preferred,
    ).start()
    try:
        assert server.port > preferred
        with urllib.request.urlopen(f"{server.url}/api/health", timeout=3) as response:
            health = json.loads(response.read())
        assert health == {"status": "ok", "run_id": RUN_ID}

        with urllib.request.urlopen(f"{server.url}/api/snapshot", timeout=3) as response:
            snapshot = json.loads(response.read())
        assert snapshot["best"]["fitness"] == 0.7
        assert snapshot["best"]["metrics"]["bad"] is None

        with urllib.request.urlopen(f"{server.url}/api/runs", timeout=3) as response:
            history = json.loads(response.read())
        assert [item["id"] for item in history["runs"]] == [RUN_ID, history_run_id]

        history_url = f"{server.url}/api/snapshot?run_id={quote(history_run_id)}"
        with urllib.request.urlopen(history_url, timeout=3) as response:
            historical_snapshot = json.loads(response.read())
        assert historical_snapshot["run"]["id"] == history_run_id
        assert historical_snapshot["best"]["fitness"] == 0.2

        with pytest.raises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(f"{server.url}/api/snapshot?run_id=missing_run", timeout=3)
        assert missing.value.code == 404
    finally:
        server.stop()
        blocker.close()


def test_dashboard_uses_remote_bind_and_public_hosts_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_EVOLVE_UI_HOST", "0.0.0.0")
    monkeypatch.setenv("AGENT_EVOLVE_UI_PUBLIC_HOST", "gpu.example.test")

    server = dashboard_module.start_dashboard(
        RUN_ID,
        workspace_root=tmp_path,
        preferred_port=0,
    )
    assert server is not None
    key = (str(tmp_path.resolve()), RUN_ID)
    try:
        assert server.host == "0.0.0.0"
        assert server.public_host == "gpu.example.test"
        assert server.url == f"http://gpu.example.test:{server.port}"
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.port}/api/health", timeout=3
        ) as response:
            assert json.loads(response.read()) == {"status": "ok", "run_id": RUN_ID}
    finally:
        server.stop()
        dashboard_module._servers.pop(key, None)


def test_dashboard_keeps_loopback_default_when_remote_hosts_are_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_EVOLVE_UI_HOST", raising=False)
    monkeypatch.delenv("AGENT_EVOLVE_UI_PUBLIC_HOST", raising=False)

    server = dashboard_module.start_dashboard(
        RUN_ID,
        workspace_root=tmp_path,
        preferred_port=0,
    )
    assert server is not None
    key = (str(tmp_path.resolve()), RUN_ID)
    try:
        assert server.host == dashboard_module.DEFAULT_DASHBOARD_HOST
        assert server.public_host == dashboard_module.DEFAULT_DASHBOARD_HOST
        assert server.url == f"http://127.0.0.1:{server.port}"
    finally:
        server.stop()
        dashboard_module._servers.pop(key, None)


def test_dashboard_includes_robust_chart_scale_controls():
    static_root = Path(__file__).parents[1] / "evotrias" / "mcp" / "dashboard_static"
    html = (static_root / "index.html").read_text(encoding="utf-8")
    javascript = (static_root / "app.js").read_text(encoding="utf-8")

    assert 'id="chart-scale-focus"' in html
    assert 'id="chart-scale-full"' in html
    assert "function computeChartDomain" in javascript
    assert "1.5 * iqr" in javascript
    assert "rawSpan < focusSpan * 2.4" in javascript
    assert "drawBoundaryMarker" in javascript


def test_dashboard_includes_flat_run_history_selector():
    static_root = Path(__file__).parents[1] / "evotrias" / "mcp" / "dashboard_static"
    html = (static_root / "index.html").read_text(encoding="utf-8")
    javascript = (static_root / "app.js").read_text(encoding="utf-8")
    stylesheet = (static_root / "styles.css").read_text(encoding="utf-8")

    assert 'id="run-selector-trigger"' in html
    assert 'id="run-history-list"' in html
    assert 'id="run-history-count"' in html
    assert 'fetch("/api/runs"' in javascript
    assert "function switchRun" in javascript
    assert "URLSearchParams(window.location.search)" in javascript
    assert 'data-run-id="${escapeHtml(run.id)}"' in javascript
    assert ".run-menu" in stylesheet
    assert ".run-history-item.current" in stylesheet


def test_dashboard_shows_feature_status_badges():
    static_root = Path(__file__).parents[1] / "evotrias" / "mcp" / "dashboard_static"
    html = (static_root / "index.html").read_text(encoding="utf-8")
    javascript = (static_root / "app.js").read_text(encoding="utf-8")
    stylesheet = (static_root / "styles.css").read_text(encoding="utf-8")

    assert 'id="guidance-status"' in html
    assert 'id="notes-status"' in html
    assert "function renderFeatureStatus" in javascript
    assert "features.meta_guidance" in javascript
    assert "features.notes" in javascript
    assert ".feature-status.enabled" in stylesheet
    assert ".feature-status.disabled" in stylesheet


def test_dashboard_uses_evotrias_branding():
    static_root = Path(__file__).parents[1] / "evotrias" / "mcp" / "dashboard_static"
    html = (static_root / "index.html").read_text(encoding="utf-8")
    stylesheet = (static_root / "styles.css").read_text(encoding="utf-8")

    assert "<strong>EvoTrias</strong>" in html
    assert 'src="/assets/evotrias-logo-v5.png"' in html
    assert "Run Observatory" not in html
    assert (static_root / "evotrias-logo.png").is_file()
    assert (static_root / "evotrias-logo-v5.png").is_file()
    assert ".brand-logo" in stylesheet


def test_lineage_tooltip_avoids_native_duplicate_and_wraps_inspirations():
    static_root = Path(__file__).parents[1] / "evotrias" / "mcp" / "dashboard_static"
    javascript = (static_root / "app.js").read_text(encoding="utf-8")
    stylesheet = (static_root / "styles.css").read_text(encoding="utf-8")

    assert "<title>${escapeHtml(title)}</title>" not in javascript
    assert 'role="img" aria-label="${escapeHtml(title)}"' in javascript
    assert "overflow-wrap: anywhere" in stylesheet
    assert (
        "text-overflow: ellipsis"
        not in stylesheet.split(".lineage-tooltip-grid span:nth-child(even)", 1)[1].split("}", 1)[0]
    )


def test_dashboard_includes_interactive_lineage_controls():
    static_root = Path(__file__).parents[1] / "evotrias" / "mcp" / "dashboard_static"
    html = (static_root / "index.html").read_text(encoding="utf-8")
    javascript = (static_root / "app.js").read_text(encoding="utf-8")
    stylesheet = (static_root / "styles.css").read_text(encoding="utf-8")

    assert 'id="lineage-viewport"' in html
    assert 'id="lineage-fullscreen"' in html
    assert 'id="lineage-fit"' in html
    assert html.index('class="knowledge-grid"') < html.index('class="lineage-section"')
    assert "function computeLineageLayout" in javascript
    assert "function showLineageHover" in javascript
    assert "requestFullscreen()" in javascript
    assert "data-inspiration-target" in javascript
    assert "lineageSuppressClick" in javascript
    assert 'addEventListener("selectstart"' in javascript
    assert "lineage-best-trophy" in javascript
    assert ".lineage-inspiration-edge.visible" in stylesheet
    assert ".lineage-node.is-best .lineage-node-shell" in stylesheet
    assert "-webkit-user-drag: none" in stylesheet
    assert ".lineage-card:fullscreen" in stylesheet

import json
import socket
import urllib.request
from pathlib import Path

from agent_evolve_mcp.dashboard import DashboardServer, build_snapshot

RUN_ID = "run_20260715010101"


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
        public / "dashboard.json",
        {
            "run_id": RUN_ID,
            "url": "http://127.0.0.1:4000",
            "max_total_tasks": 50,
            "task_description": "Improve the benchmark candidate.",
            "started_at": "2026-07-15T01:00:00+00:00",
        },
    )
    _write_json(
        public / "scheduler_state.json",
        {
            "run_id": RUN_ID,
            "running_tasks": ["task_003"],
            "task_agents": {"task_003": {"agent_id": "agent-live-3", "status": "running"}},
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
        state / "run_state.json",
        {
            "run_id": RUN_ID,
            "search_state": "watching",
            "started_at": "2026-07-15T01:00:00+00:00",
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


def test_dashboard_uses_next_port_and_serves_snapshot(tmp_path, monkeypatch):
    _seed_run(tmp_path, monkeypatch)
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
    finally:
        server.stop()
        blocker.close()


def test_dashboard_includes_robust_chart_scale_controls():
    static_root = Path(__file__).parents[1] / "agent_evolve_mcp" / "dashboard_static"
    html = (static_root / "index.html").read_text(encoding="utf-8")
    javascript = (static_root / "app.js").read_text(encoding="utf-8")

    assert 'id="chart-scale-focus"' in html
    assert 'id="chart-scale-full"' in html
    assert "function computeChartDomain" in javascript
    assert "1.5 * iqr" in javascript
    assert "rawSpan < focusSpan * 2.4" in javascript
    assert "drawBoundaryMarker" in javascript


def test_dashboard_includes_interactive_lineage_controls():
    static_root = Path(__file__).parents[1] / "agent_evolve_mcp" / "dashboard_static"
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

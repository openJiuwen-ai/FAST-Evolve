import json
import socket
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import pytest

import evotrias.dashboard.server as dashboard_module
import evotrias.mcp.public_tools as public_tools_module
from evotrias.dashboard.server import DashboardServer, build_snapshot, list_runs
from evotrias.db.attempts import AttemptRecord, Attempts
from evotrias.db.config import Config
from evotrias.db.meta_guidance_state import Assignment, Guidance, MetaGuidanceState
from evotrias.db.notes import Note, Notes
from evotrias.db.run_state import RunState
from evotrias.db.task_contexts import TaskContexts
from evotrias.db.task_pool import TaskAssigned, TaskCompleted, TaskFailed, TaskPool
from evotrias.enums import SearchState

RUN_ID = "signal_processing_20260715010101"


@pytest.fixture(autouse=True)
def cwd(monkeypatch, tmp_path):
    # db paths are cwd-relative; every test gets a fresh workspace.
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _attempt(run_id: str, task_id: str, *, fitness: float, generation: int, parent_id, created_at: str, metrics=None) -> AttemptRecord:
    return AttemptRecord(
        id="attempt_001",
        run_id=run_id,
        task_id=task_id,
        subagent_id="agent",
        artifact_ref=f"runs/{run_id}/tasks/{task_id}/attempts/attempt_001/solution.py",
        parent_id=parent_id,
        inspiration_ids=[],
        generation=generation,
        verification_result={"status": "success", "fitness": fitness, "metrics": metrics or {}},
        in_success_pool=True,
        created_at=created_at,
    )


def _seed_run():
    """A run mid-flight: two successes, one running task under meta-guidance."""
    with Config(RUN_ID) as config:
        config.budget.max_total_tasks = 50
        config.meta_evolve.enabled = True
        config.notes.enabled = False
    with TaskPool(RUN_ID) as pool:
        pool.running["task_003"] = TaskAssigned(agent_id="agent-live-3", at="2026-07-15T01:02:50+00:00")
    log = Attempts(RUN_ID)
    log.append(_attempt(RUN_ID, "task_001", fitness=0.4, generation=0, parent_id=None,
                        created_at="2026-07-15T01:01:00+00:00",
                        metrics={"accuracy_score": 0.4, "bad": float("nan")}))
    log.append(_attempt(RUN_ID, "task_002", fitness=0.7, generation=1, parent_id="task_001",
                        created_at="2026-07-15T01:02:00+00:00",
                        metrics={"accuracy_score": 0.7, "bad": float("nan")}))
    with RunState(RUN_ID) as state:
        state.search_state = SearchState.WATCHING
        state.task_description = "Improve the benchmark candidate."
        state.diagnostics = {"plateau_count": 3, "patience": 4, "current_diversity": 0.82}
    with TaskContexts(RUN_ID) as contexts:
        context = contexts.get("task_003", create=True)
        context.parent_id = "task_002"
        context.inspiration_ids = ["task_001"]
    Notes(RUN_ID).append(
        Note(
            note_id="note_positive_1",
            note_type="positive",
            title="Keep the stable update",
            lesson="It improved fitness.",
            created_at="2026-07-15T01:02:30+00:00",
        )
    )
    with MetaGuidanceState(RUN_ID) as meta:
        meta.active_guidance = [
            Guidance(
                idea="Try a new search family",
                description="Escape the current plateau.",
                novelty_claim="",
                approach_type="",
                guidance_id="mg_0001",
                created_at="2026-07-15T01:02:40+00:00",
                uses=1,
                max_uses=2,
            )
        ]
        meta.task_assignments = {
            "task_003": Assignment(task_id="task_003", guidance_id="mg_0001", anchor_parent_id="task_002")
        }


def _seed_history_run() -> str:
    run_id = "run_20260714010101"
    with TaskPool(run_id):
        pass
    Attempts(run_id).append(
        _attempt(run_id, "task_001", fitness=0.2, generation=0, parent_id=None,
                 created_at="2026-07-14T01:02:00+00:00")
    )
    with RunState(run_id) as state:
        state.search_state = SearchState.STOPPED
        state.task_description = "An older persisted run."
    return run_id


def test_build_snapshot_aggregates_live_run_state(tmp_path):
    _seed_run()

    snapshot = build_snapshot(RUN_ID)

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

    activity, detail = dashboard_module._infer_activity(task_dir, None, None, None)

    assert activity == "Verifying candidate"
    assert "attempt_001" in detail


def test_recent_activity_links_to_evotrias_state_machine(tmp_path):
    """§5.4: Recent activity shows completed(fitness) / failed(reason) sourced
    from .evotrias/db/runs/<run_id>/task_pool.json, not just the legacy
    success log."""
    _seed_run()

    # Seed the evotrias task pool with a completed task (fitness) and a failed
    # task (reason). The dashboard must surface both in Recent activity.
    with TaskPool(RUN_ID) as pool:
        pool.running.clear()
        pool.completed["task_004"] = TaskCompleted(fitness=0.91)
        pool.failed["task_005"] = TaskFailed(reason="candidate did not compile")

    snapshot = build_snapshot(RUN_ID)
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

    monkeypatch.setenv("EVOTRIAS_META_EVOLVE_ENABLED", "0")
    monkeypatch.setenv("EVOTRIAS_NOTES_ENABLED", "0")
    monkeypatch.setattr(public_tools_module, "start_dashboard", fake_start_dashboard)

    result = public_tools_module.evolve_configure_run(run_id, max_total_tasks=20)

    assert result["meta_evolve"]["enabled"] is False
    config = Config(run_id)
    assert config.meta_evolve.enabled is False
    assert config.notes.enabled is False
    # The dashboard reads flags from config.json itself; nothing is passed in.
    assert captured == {"args": (run_id,), "kwargs": {}}


def test_list_runs_returns_flat_history_for_named_and_legacy_ids(tmp_path, monkeypatch):
    _seed_run()
    history_run_id = _seed_history_run()
    monkeypatch.setattr(
        dashboard_module,
        "_utc_now",
        lambda: datetime(2026, 7, 15, 1, 4, tzinfo=timezone.utc),
    )

    history = list_runs(RUN_ID)

    assert history["current"] == RUN_ID
    assert [item["id"] for item in history["runs"]] == [RUN_ID, history_run_id]
    assert history["runs"][0]["label"] == "signal_processing"
    assert history["runs"][0]["timestamp"] == "20260715010101"
    assert history["runs"][0]["status"] == "running"
    assert history["runs"][0]["completed_tasks"] == 2
    assert history["runs"][0]["best_fitness"] == 0.7
    assert history["runs"][1]["label"] == "run"
    assert history["runs"][1]["status"] == "stopped"


def test_dashboard_uses_next_port_and_serves_snapshot(tmp_path):
    _seed_run()
    history_run_id = _seed_history_run()
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    preferred = blocker.getsockname()[1]
    if preferred >= 65535:
        blocker.close()
        return

    server = DashboardServer(run_id=RUN_ID, preferred_port=preferred).start()
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
    monkeypatch.setenv("EVOTRIAS_UI_HOST", "0.0.0.0")
    monkeypatch.setenv("EVOTRIAS_UI_PUBLIC_HOST", "gpu.example.test")

    server = dashboard_module.start_dashboard(RUN_ID, preferred_port=0)
    assert server is not None
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
        dashboard_module._servers.pop(RUN_ID, None)


def test_dashboard_keeps_loopback_default_when_remote_hosts_are_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("EVOTRIAS_UI_HOST", raising=False)
    monkeypatch.delenv("EVOTRIAS_UI_PUBLIC_HOST", raising=False)

    server = dashboard_module.start_dashboard(RUN_ID, preferred_port=0)
    assert server is not None
    try:
        assert server.host == dashboard_module.DEFAULT_DASHBOARD_HOST
        assert server.public_host == dashboard_module.DEFAULT_DASHBOARD_HOST
        assert server.url == f"http://127.0.0.1:{server.port}"
    finally:
        server.stop()
        dashboard_module._servers.pop(RUN_ID, None)


def test_dashboard_includes_robust_chart_scale_controls():
    static_root = Path(__file__).parents[1] / "evotrias" / "dashboard" / "static"
    html = (static_root / "index.html").read_text(encoding="utf-8")
    javascript = (static_root / "app.js").read_text(encoding="utf-8")

    assert 'id="chart-scale-focus"' in html
    assert 'id="chart-scale-full"' in html
    assert "function computeChartDomain" in javascript
    assert "1.5 * iqr" in javascript
    assert "rawSpan < focusSpan * 2.4" in javascript
    assert "drawBoundaryMarker" in javascript


def test_dashboard_includes_flat_run_history_selector():
    static_root = Path(__file__).parents[1] / "evotrias" / "dashboard" / "static"
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
    static_root = Path(__file__).parents[1] / "evotrias" / "dashboard" / "static"
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
    static_root = Path(__file__).parents[1] / "evotrias" / "dashboard" / "static"
    html = (static_root / "index.html").read_text(encoding="utf-8")
    stylesheet = (static_root / "styles.css").read_text(encoding="utf-8")

    assert "<strong>EvoTrias</strong>" in html
    assert 'src="/assets/evotrias-logo.png"' in html
    assert "Run Observatory" not in html
    assert (static_root / "evotrias-logo.png").is_file()
    assert ".brand-logo" in stylesheet


def test_lineage_tooltip_avoids_native_duplicate_and_wraps_inspirations():
    static_root = Path(__file__).parents[1] / "evotrias" / "dashboard" / "static"
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
    static_root = Path(__file__).parents[1] / "evotrias" / "dashboard" / "static"
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

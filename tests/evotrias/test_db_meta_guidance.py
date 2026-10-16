"""Tests for evotrias.db.meta_guidance_state and evotrias.db.meta_guidance_events."""

from __future__ import annotations

import json

import pytest

from evotrias.db import paths
from evotrias.db.meta_guidance_events import MetaGuidanceEvent, MetaGuidanceEvents
from evotrias.db.meta_guidance_state import (
    Assignment,
    Guidance,
    MetaGuidanceState,
    PendingRequest,
    RequestProgress,
    Watching,
)

RUN_ID = "mg_test"


@pytest.fixture(autouse=True)
def cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _guidance(gid: str, **overrides) -> Guidance:
    fields = dict(idea="i", description="d", novelty_claim="n", approach_type="a", guidance_id=gid, max_uses=2)
    fields.update(overrides)
    return Guidance(**fields)


def test_empty_state_defaults():
    state = MetaGuidanceState(RUN_ID)
    assert state.watching is None
    assert state.pending_request is None
    assert state.active_guidance == [] and state.tried_guidance == []
    assert state.task_assignments == {}
    assert (state.next_guidance_index, state.next_request_index) == (1, 1)
    assert state.active_by_id("mg_0001") is None
    assert MetaGuidanceState.next_id("mg", 7) == "mg_0007"


def test_round_trip_rebuilds_every_nested_shape():
    with MetaGuidanceState(RUN_ID) as state:
        state.watching = Watching(started_task_count=4, best_fitness=0.5)
        state.pending_request = PendingRequest(
            request_id="mgr_0001",
            created_at_task_count=4,
            current_best={"record_id": "task_001", "fitness": 0.5},
            progress=RequestProgress(completed_since_watching=2, current_best_fitness=0.5),
            constraints=["x"],
        )
        state.active_guidance.append(_guidance("mg_0001"))
        state.tried_guidance.append(
            _guidance("mg_0000", status="exhausted", outcome="failed", outcomes=[{"fitness": None}])
        )
        state.task_assignments["task_002"] = Assignment(
            task_id="task_002", guidance_id="mg_0001", anchor_parent_id="task_001"
        )
        state.next_guidance_index = 2

    reloaded = MetaGuidanceState(RUN_ID)
    assert isinstance(reloaded.watching, Watching) and reloaded.watching.started_task_count == 4
    assert isinstance(reloaded.pending_request, PendingRequest)
    assert isinstance(reloaded.pending_request.progress, RequestProgress)
    assert reloaded.pending_request.progress.completed_since_watching == 2
    assert reloaded.pending_request.current_best == {"record_id": "task_001", "fitness": 0.5}
    assert isinstance(reloaded.active_guidance[0], Guidance)
    assert reloaded.active_by_id("mg_0001").max_uses == 2
    assert reloaded.tried_guidance[0].outcome == "failed"
    assert isinstance(reloaded.task_assignments["task_002"], Assignment)
    assert reloaded.task_assignments["task_002"].status == "assigned"
    assert reloaded.next_guidance_index == 2

    on_disk = json.loads((paths.run_dir(RUN_ID) / paths.META_GUIDANCE_STATE).read_text(encoding="utf-8"))
    assert on_disk["watching"]["best_fitness"] == 0.5
    assert on_disk["task_assignments"]["task_002"]["guidance_id"] == "mg_0001"


def test_guidance_committed_fields_default_to_fresh():
    g = _guidance("mg_0001")
    assert (g.uses, g.status, g.has_valid_result, g.outcomes, g.outcome) == (0, "active", False, [], None)
    assert g.target_metric == "fitness"


def test_events_append_and_iterate():
    events = MetaGuidanceEvents(RUN_ID)
    events.append(MetaGuidanceEvent(event_type="watching_started", run_id=RUN_ID, payload={"started_task_count": 3}))
    events.append(MetaGuidanceEvent(event_type="assigned", run_id=RUN_ID, payload={"task_id": "task_002"}))
    got = list(events)
    assert [e.event_type for e in got] == ["watching_started", "assigned"]
    assert got[1].payload == {"task_id": "task_002"}
    assert (paths.run_dir(RUN_ID) / paths.META_GUIDANCE_EVENTS).exists()

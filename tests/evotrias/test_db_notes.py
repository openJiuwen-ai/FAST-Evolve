# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Tests for evotrias.db.notes, evotrias.db.note_requests, evotrias.db.notes_events."""

from __future__ import annotations

import json

import pytest

from evotrias.db import paths
from evotrias.db.note_requests import NoteRequest, NoteRequests
from evotrias.db.notes import Note, NoteRefs, Notes, NoteSource, NoteTrigger
from evotrias.db.notes_events import NotesEvent, NotesEvents

RUN_ID = "notes_test"


@pytest.fixture(autouse=True)
def cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _note(note_id: str, note_type: str = "positive", **overrides) -> Note:
    fields = dict(
        note_id=note_id,
        note_type=note_type,
        title="t",
        lesson="l",
        actionable_guidance="g",
        evidence_summary="e",
        source=NoteSource(
            run_id=RUN_ID, task_id="task_001", attempt_id="attempt_001", parent_success_record_id="task_000"
        ),
        trigger=NoteTrigger(kind="material_improvement", relative_delta=0.1, child_fitness=1.1, parent_fitness=1.0),
        refs=NoteRefs(child_artifact_ref="c.py", parent_artifact_ref="p.py"),
    )
    fields.update(overrides)
    return Note(**fields)


def test_notes_empty():
    notes = Notes(RUN_ID)
    assert list(notes) == []
    assert notes.get("x") is None
    assert notes.by_type("positive") == []
    assert not (paths.run_dir(RUN_ID) / paths.NOTES).exists()


def test_notes_round_trip_rebuilds_nested_records():
    notes = Notes(RUN_ID)
    notes.append(_note("n1"))
    notes.append(_note("n2", note_type="error"))
    got = notes.get("n1")
    assert isinstance(got, Note)
    assert isinstance(got.source, NoteSource) and got.source.parent_success_record_id == "task_000"
    assert isinstance(got.trigger, NoteTrigger) and got.trigger.child_fitness == 1.1
    assert isinstance(got.refs, NoteRefs) and got.refs.child_artifact_ref == "c.py"
    assert [n.note_id for n in notes.by_type("error")] == ["n2"]
    on_disk = (paths.run_dir(RUN_ID) / paths.NOTES).read_text(encoding="utf-8").splitlines()
    assert len(on_disk) == 2
    assert json.loads(on_disk[0])["trigger"]["kind"] == "material_improvement"


def test_note_requests_get_returns_last_match():
    requests = NoteRequests(RUN_ID)
    assert requests.get("r1") is None
    base = dict(note_id="n1", run_id=RUN_ID, task_id="task_001", attempt_id="attempt_001", note_type="positive")
    requests.append(NoteRequest(note_request_id="r1", analysis_prompt="first", **base))
    requests.append(NoteRequest(note_request_id="r2", analysis_prompt="other", **base))
    requests.append(NoteRequest(note_request_id="r1", analysis_prompt="second", **base))
    got = requests.get("r1")
    assert isinstance(got, NoteRequest)
    assert got.analysis_prompt == "second"
    assert isinstance(got.trigger, NoteTrigger)


def test_notes_events_append_and_iterate():
    events = NotesEvents(RUN_ID)
    events.append(
        NotesEvent(
            event_type="note_request_skipped", run_id=RUN_ID, payload={"reason": "warmup", "task_id": "task_001"}
        )
    )
    events.append(NotesEvent(event_type="note_committed", run_id=RUN_ID, payload={"note_id": "n1"}))
    got = list(events)
    assert [e.event_type for e in got] == ["note_request_skipped", "note_committed"]
    assert got[0].payload == {"reason": "warmup", "task_id": "task_001"}
    assert got[0].created_at

# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Requests for the agent to distil a note, persisted as ``note_requests.jsonl``.

Appended by the controller when an attempt meets a note trigger; consumed by
``commit_generated_note`` which looks the request up by id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evotrias.db import paths
from evotrias.db.base import DBList
from evotrias.db.notes import NoteRefs, NoteSource, NoteTrigger
from evotrias.utils.time import utc_now_iso


@dataclass
class NoteRequest:
    note_request_id: str
    note_id: str
    run_id: str
    task_id: str
    attempt_id: str
    note_type: str
    trigger: NoteTrigger = field(default_factory=NoteTrigger)
    source: NoteSource = field(default_factory=NoteSource)
    refs: NoteRefs = field(default_factory=NoteRefs)
    analysis_prompt: str = ""
    expected_schema: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)


class NoteRequests(DBList):
    def __init__(self, run_id: str):
        super().__init__(paths.run_dir(run_id) / paths.NOTE_REQUESTS, NoteRequest)

    def get(self, note_request_id: str) -> NoteRequest | None:
        """Latest request with this id (a request is only ever appended once, but be safe)."""
        match = None
        for request in self:
            if request.note_request_id == note_request_id:
                match = request
        return match

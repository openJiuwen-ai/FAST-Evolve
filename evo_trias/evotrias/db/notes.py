"""Run-local Notes committed by the agent, persisted as ``notes.jsonl``.

One line per committed note.  The three nested records describe why the
note was requested (``trigger``), which attempt produced it (``source``) and
what artifacts back it (``refs``); they are copied from the originating
``NoteRequest`` at commit time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from evotrias.db import paths
from evotrias.db.base import DBList
from evotrias.utils.time import utc_now_iso


@dataclass
class NoteTrigger:
    kind: str = ""
    relative_delta: Optional[float] = None
    threshold: Optional[float] = None
    child_fitness: Optional[float] = None
    parent_fitness: Optional[float] = None
    # Diagnostics that vary by note type (positive notes carry gate details).
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NoteSource:
    run_id: str = ""
    task_id: str = ""
    attempt_id: str = ""
    success_record_id: Optional[str] = None
    parent_success_record_id: Optional[str] = None
    inspiration_success_record_ids: List[str] = field(default_factory=list)


@dataclass
class NoteRefs:
    child_artifact_ref: str = ""
    parent_artifact_ref: str = ""
    inspiration_artifact_refs: List[str] = field(default_factory=list)
    verification_result_ref: Optional[str] = None
    failed_attempt_refs: List[str] = field(default_factory=list)
    failed_verification_result_refs: List[str] = field(default_factory=list)


@dataclass
class Note:
    note_id: str
    note_type: str
    title: str = ""
    lesson: str = ""
    actionable_guidance: str = ""
    evidence_summary: str = ""
    confidence: str = "medium"
    source: NoteSource = field(default_factory=NoteSource)
    trigger: NoteTrigger = field(default_factory=NoteTrigger)
    refs: NoteRefs = field(default_factory=NoteRefs)
    created_at: str = field(default_factory=utc_now_iso)


class Notes(DBList):
    def __init__(self, run_id: str):
        super().__init__(paths.run_dir(run_id) / paths.NOTES, Note)

    def get(self, note_id: str) -> Optional[Note]:
        for note in self:
            if note.note_id == note_id:
                return note
        return None

    def by_type(self, note_type: str) -> List[Note]:
        return [note for note in self if note.note_type == note_type]

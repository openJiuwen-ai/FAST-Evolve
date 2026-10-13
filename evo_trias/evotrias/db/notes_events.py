"""Complete timeline of the Notes subsystem, persisted as ``notes_events.jsonl``.

Deliberately overlaps with ``note_requests.jsonl`` and ``notes.jsonl``: one
file is the whole story (requested, skipped, committed, rejected) so the
dashboard reads a single ordered stream.  ``payload`` holds the fields that
differ per ``event_type``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evotrias.db import paths
from evotrias.db.base import DBList
from evotrias.utils.time import utc_now_iso


@dataclass
class NotesEvent:
    event_type: str
    run_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)


class NotesEvents(DBList):
    def __init__(self, run_id: str):
        super().__init__(paths.run_dir(run_id) / paths.NOTES_EVENTS, NotesEvent)

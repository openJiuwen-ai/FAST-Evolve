"""Every final task attempt, persisted as ``attempts.jsonl``.

Written once per attempt by ``record_attempt``; the first-hand record that
``SuccessDB`` and the run report are derived from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from evotrias.db import paths
from evotrias.db.base import DBList
from evotrias.utils.time import utc_now_iso


@dataclass
class AttemptRecord:
    """A completed task attempt, successful or not.

    Attempts are broader than SuccessRecords.  Every final task result should be
    recorded as an attempt for audit and future policy evidence.  Only attempts
    with successful verification and a valid fitness enter SuccessDB.
    """

    id: str
    run_id: str
    task_id: str
    subagent_id: str
    artifact_ref: str
    parent_id: Optional[str]
    inspiration_ids: List[str]
    generation: Optional[int]
    verification_result: Dict[str, Any]
    meta_guidance_id: Optional[str] = None
    anchor_parent_id: Optional[str] = None
    verification_result_ref: Optional[str] = None
    sketch: Optional[str] = None
    entered_success_db: bool = False
    success_record_id: Optional[str] = None
    created_at: str = field(default_factory=utc_now_iso)


class Attempts(DBList):
    def __init__(self, run_id: str):
        super().__init__(paths.run_dir(run_id) / paths.ATTEMPTS, AttemptRecord)

    @property
    def successes(self) -> Dict[str, AttemptRecord]:
        """task_id -> the attempt that entered SuccessDB.

        A task has one such attempt at most (``record_attempt`` marks only the
        first success), so its presence is the definition of "task succeeded".
        Re-reads the file on every access.
        """
        return {a.task_id: a for a in self if a.entered_success_db}

    def find_by_task_id(self, task_id: str) -> Optional[AttemptRecord]:
        """Most recent attempt for a task, if any."""
        match = None
        for attempt in self:
            if attempt.task_id == task_id:
                match = attempt
        return match

    def find_by_task_attempt(self, task_id: str, attempt_id: str) -> Optional[AttemptRecord]:
        for attempt in self:
            if attempt.task_id == task_id and attempt.id == attempt_id:
                return attempt
        return None

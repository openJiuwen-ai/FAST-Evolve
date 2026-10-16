"""Every final task attempt, persisted as ``attempts.jsonl``.

Written once per attempt by ``record_attempt``; the first-hand record that
``SuccessPool`` and the run report are derived from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evotrias.db import paths
from evotrias.db.base import DBList
from evotrias.utils.time import utc_now_iso


@dataclass
class AttemptRecord:
    """A completed task attempt, successful or not.

    Attempts are broader than SuccessRecords.  Every final task result should be
    recorded as an attempt for audit and future policy evidence.  Only attempts
    with successful verification and a valid fitness enter the success pool.
    """

    id: str
    run_id: str
    task_id: str
    subagent_id: str
    artifact_ref: str
    parent_id: str | None
    inspiration_ids: list[str]
    generation: int | None
    verification_result: dict[str, Any]
    meta_guidance_id: str | None = None
    anchor_parent_id: str | None = None
    verification_result_ref: str | None = None
    sketch: str | None = None
    in_success_pool: bool = False
    success_record_id: str | None = None
    created_at: str = field(default_factory=utc_now_iso)


class Attempts(DBList):
    def __init__(self, run_id: str):
        super().__init__(paths.run_dir(run_id) / paths.ATTEMPTS, AttemptRecord)

    @property
    def successes(self) -> dict[str, AttemptRecord]:
        """task_id -> the attempt that entered the success pool.

        A task has one such attempt at most (``record_attempt`` marks only the
        first success), so its presence is the definition of "task succeeded".
        Re-reads the file on every access.
        """
        return {a.task_id: a for a in self if a.in_success_pool}

    @property
    def final_by_task(self) -> dict[str, AttemptRecord]:
        """task_id -> the attempt that stands for the task's outcome.

        The attempt that entered the success pool when there is one (see
        ``successes``), otherwise the task's latest attempt.  Insertion order
        follows first appearance in the log.  Re-reads the file on every access.
        """
        final = {attempt.task_id: attempt for attempt in self}
        final.update(self.successes)
        return final

    def find_by_task_id(self, task_id: str) -> AttemptRecord | None:
        """Most recent attempt for a task, if any."""
        match = None
        for attempt in self:
            if attempt.task_id == task_id:
                match = attempt
        return match

    def find_by_task_attempt(self, task_id: str, attempt_id: str) -> AttemptRecord | None:
        for attempt in self:
            if attempt.task_id == task_id and attempt.id == attempt_id:
                return attempt
        return None

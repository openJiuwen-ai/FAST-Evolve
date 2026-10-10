"""The scheduler's task state machine, persisted as ``task_pool.json``.

Task ids move through the buckets ``created -> pending -> queued -> running
-> completed | failed``; each bucket past ``pending`` records when the task
entered it and, for ``queued``/``running``, which agent holds it.  Written by hooks (allocate/queue/start/complete/fail) and by the
controller (top-up); ``evotrias.scheduler`` owns the transitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from evotrias.db import paths
from evotrias.db.base import DBObject
from evotrias.utils.time import utc_now_iso


@dataclass
class TaskAssigned:
    """A task bound to an agent (``queued`` before it starts, ``running`` after)."""

    agent_id: str
    at: str = field(default_factory=utc_now_iso)


@dataclass
class TaskCompleted:
    fitness: Optional[float] = None
    at: str = field(default_factory=utc_now_iso)


@dataclass
class TaskFailed:
    reason: str = ""
    at: str = field(default_factory=utc_now_iso)


class TaskPool(DBObject):
    created: List[str] = field(default_factory=list)
    pending: List[str] = field(default_factory=list)
    queued: Dict[str, TaskAssigned] = field(default_factory=dict)
    running: Dict[str, TaskAssigned] = field(default_factory=dict)
    completed: Dict[str, TaskCompleted] = field(default_factory=dict)
    failed: Dict[str, TaskFailed] = field(default_factory=dict)
    next_seq: int = 1

    def __init__(self, run_id: str):
        super().__init__(paths.run_dir(run_id) / paths.TASK_POOL)

    def next_task_id(self) -> str:
        """Mint the next task id; persists only when called inside ``with``."""
        seq = self.next_seq
        self.next_seq = seq + 1
        return f"task_{seq:03d}"

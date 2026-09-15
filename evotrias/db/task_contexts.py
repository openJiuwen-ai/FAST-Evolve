"""Per-task evolutionary context, persisted as ``task_contexts.json``.

Selection tools fill a task's context (parent, inspirations, meta guidance);
``record_attempt`` reads it back and marks the task recorded.  Keeping it here
lets the public MCP contract omit ``parent_id``/``inspiration_ids`` from later
tool calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, overload

from evotrias.db import paths
from evotrias.db.base import DBObject
from evotrias.utils.time import utc_now_iso


@dataclass
class TaskContext:
    """Pre-record mapping from one task to selected evolutionary context.

    The public MCP contract keeps parent and inspiration ids out of later tool
    calls.  This small persisted object is the bridge: selection tools fill it,
    and ``record_attempt`` reads it back when the verifier result is ready.
    """

    task_id: str
    parent_id: Optional[str] = None
    inspiration_ids: List[str] = field(default_factory=list)
    meta_guidance_id: Optional[str] = None
    anchor_parent_id: Optional[str] = None
    parent_selected: bool = False
    inspirations_selected: bool = False
    missing_parent_context: bool = False
    recorded: bool = False
    verification_result_ref: Optional[str] = None
    entered_success_db: Optional[bool] = None
    success_record_id: Optional[str] = None
    created_at: str = field(default_factory=utc_now_iso)


class TaskContexts(DBObject):
    tasks: Dict[str, TaskContext] = field(default_factory=dict)

    def __init__(self, run_id: str):
        super().__init__(paths.run_dir(run_id) / paths.TASK_CONTEXTS)

    def __len__(self) -> int:
        return len(self.tasks)

    @overload
    def get(self, task_id: str, *, create: Literal[False] = False) -> Optional[TaskContext]: ...

    @overload
    def get(self, task_id: str, *, create: Literal[True]) -> TaskContext: ...

    def get(self, task_id: str, *, create: bool = False) -> Optional[TaskContext]:
        """The task's context; with ``create=True`` a blank one is added in memory if absent."""
        context = self.tasks.get(task_id)
        if context is None and create:
            context = TaskContext(task_id=task_id)
            self.tasks[task_id] = context
        return context

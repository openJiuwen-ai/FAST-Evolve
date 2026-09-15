"""Persistence for controller-local task context.

The public MCP interface deliberately avoids asking agents to remember
``parent_id`` / ``inspiration_ids`` or convergence internals.  This module is
where those details live between tool calls.
"""

from __future__ import annotations

from typing import Dict, Optional

from evotrias.db.models import TaskContext
from evotrias.mcp.storage import read_json, task_contexts_path, write_json_atomic


class TaskContextStore:
    """JSON-backed mapping from task id to pre-record selection context."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.path = task_contexts_path(run_id)
        self._contexts: Dict[str, TaskContext] = {}
        self.load()

    def load(self) -> None:
        """Refresh contexts from disk."""
        data = read_json(self.path, {})
        # Older experiments may choose to wrap the map under "tasks"; accepting
        # both shapes keeps the state file easy to evolve.
        raw_contexts = data.get("tasks") if "tasks" in data else data
        self._contexts = {
            task_id: TaskContext.from_dict({"task_id": task_id, **dict(value)})
            for task_id, value in raw_contexts.items()
        }

    def save(self) -> None:
        """Persist contexts in the compact task-id keyed shape."""
        write_json_atomic(
            self.path,
            {
                task_id: context.to_dict()
                for task_id, context in sorted(self._contexts.items())
            },
        )

    def get(self, task_id: str) -> Optional[TaskContext]:
        """Return a context by task id, or None when absent."""
        return self._contexts.get(task_id)

    def ensure(self, task_id: str) -> TaskContext:
        """Return an existing context or create a blank one."""
        context = self._contexts.get(task_id)
        if context is None:
            context = TaskContext(task_id=task_id)
            self._contexts[task_id] = context
            self.save()
        return context

    def put(self, context: TaskContext) -> None:
        """Persist an updated context."""
        context.touch()
        self._contexts[context.task_id] = context
        self.save()

    def count(self) -> int:
        """Return how many tasks have been registered with the controller."""
        return len(self._contexts)

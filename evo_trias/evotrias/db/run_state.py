"""Per-run controller state, persisted as ``run_state.json``.

First written by ``configure_run`` (that write is the run's start time,
``created_at``); rewritten by ``check_convergence`` and ``finalize_run``.
Run configuration lives in ``config.json``, not here.
"""

from __future__ import annotations

from dataclasses import field
from datetime import UTC, datetime
from typing import Any

from evotrias.db import paths
from evotrias.db.base import DBObject
from evotrias.enums_and_consts import SearchState


class RunState(DBObject):
    task_description: str = ""
    search_state: SearchState = SearchState.EXPLORING
    stop_reason: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)
    last_checked_attempt_count: int = 0

    def __init__(self, run_id: str):
        super().__init__(paths.run_dir(run_id) / paths.RUN_STATE)

    def elapsed_seconds(self) -> float | None:
        """Wall-clock seconds since the run started; ``None`` before configure_run."""
        if not self.created_at:
            return None
        try:
            started = datetime.fromisoformat(self.created_at)
        except ValueError:
            return None
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        return (datetime.now(UTC) - started).total_seconds()

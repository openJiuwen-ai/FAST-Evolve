"""Per-run controller state, persisted as ``run_state.json``.

First written by ``configure_run`` (that write is the run's start time,
``created_at``); rewritten by ``check_convergence`` and ``finalize_run``.
Run configuration lives in ``config.json``, not here.
"""

from __future__ import annotations

from dataclasses import field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from evotrias.db import paths
from evotrias.db.base import DBObject
from evotrias.enums import SearchState


class RunState(DBObject):
    task_description: str = ""
    search_state: str = SearchState.EXPLORING.value
    stop_reason: str = ""
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    last_checked_attempt_count: int = 0

    def __init__(self, run_id: str):
        super().__init__(paths.run_dir(run_id) / paths.RUN_STATE)

    def elapsed_seconds(self) -> Optional[float]:
        """Wall-clock seconds since the run started; ``None`` before configure_run."""
        if not self.created_at:
            return None
        try:
            started = datetime.fromisoformat(self.created_at)
        except ValueError:
            return None
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - started).total_seconds()

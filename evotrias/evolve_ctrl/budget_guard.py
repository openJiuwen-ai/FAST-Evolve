"""Hard task and time budget checks for an evolution run."""

from __future__ import annotations

from typing import Optional

from evotrias.db.config import RunBudgetConfig


class RunBudgetGuard:
    """Return a stop reason when a hard run budget is exhausted."""

    def __init__(self, config: RunBudgetConfig):
        self.max_total_tasks = config.max_total_tasks
        self.max_time_seconds = config.max_time_seconds

    def exhaustion_reason(
        self,
        *,
        total_tasks: int,
        elapsed_seconds: Optional[float] = None,
    ) -> str:
        """Return an empty string while the run remains within budget."""
        if total_tasks >= self.max_total_tasks:
            return f"Reached max_total_tasks ({self.max_total_tasks})"
        if (
            self.max_time_seconds is not None
            and elapsed_seconds is not None
            and elapsed_seconds >= self.max_time_seconds
        ):
            return f"Time budget exhausted ({self.max_time_seconds}s)"
        return ""

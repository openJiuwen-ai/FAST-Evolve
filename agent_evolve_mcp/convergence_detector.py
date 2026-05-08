"""Multi-dimensional convergence detection.

The detector is intentionally lightweight and deterministic.  It checks hard
budget limits first, then uses three signals from the SuccessDB:

S1: best-fitness improvement over a rolling window.
S2: diversity trend over the same window.
S3: current lineage diversity as an activity proxy.

The MVP does not implement heartbeat.  WATCHING simply means selectors receive
a strategy signal that increases exploration.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from agent_evolve_mcp.lineage_tree import LineageTree
from agent_evolve_mcp.models import ConvergenceConfig, SearchState, StrategySignal, SuccessRecord
from agent_evolve_mcp.success_db import SuccessDB


class MultiDimensionalConvergenceDetector:
    """State machine that returns EXPLORING, WATCHING, or STOPPED."""

    def __init__(self, config: ConvergenceConfig):
        self.window = config.convergence_window
        self.perf_threshold = config.perf_improvement_threshold
        self.diversity_threshold = config.diversity_change_threshold
        self.activity_threshold = config.activity_threshold
        self.patience = config.patience
        self.max_total_tasks = config.max_total_tasks
        self.max_time_seconds = config.max_time_seconds
        self._state = SearchState.EXPLORING
        self._stop_reason = ""
        self._diagnostics = {}

    @property
    def state(self) -> SearchState:
        return self._state

    @property
    def stop_reason(self) -> str:
        return self._stop_reason

    def restore_state(
        self,
        state: str,
        stop_reason: str = "",
        diagnostics: Optional[dict] = None,
    ) -> None:
        """Restore persisted detector state before a selection call."""
        try:
            self._state = SearchState(state)
        except ValueError:
            self._state = SearchState.EXPLORING
        self._stop_reason = stop_reason
        self._diagnostics = dict(diagnostics or {})

    def evaluate(
        self,
        db: SuccessDB,
        lineage_tree: LineageTree,
        total_submitted: int,
        total_success: int,
        elapsed_seconds: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """Evaluate convergence from the current SuccessDB snapshot.

        ``total_submitted`` is still the hard safety valve.  The softer
        convergence logic only runs after enough successful records exist to
        compute a meaningful window.
        """
        if total_submitted >= self.max_total_tasks:
            self._state = SearchState.STOPPED
            self._stop_reason = f"Reached max_total_tasks ({self.max_total_tasks})"
            self._diagnostics = self._build_diagnostics(db, lineage_tree)
            return True, self._stop_reason

        if (
            self.max_time_seconds is not None
            and elapsed_seconds is not None
            and elapsed_seconds >= self.max_time_seconds
        ):
            self._state = SearchState.STOPPED
            self._stop_reason = f"Time budget exhausted ({self.max_time_seconds}s)"
            self._diagnostics = self._build_diagnostics(db, lineage_tree)
            return True, self._stop_reason

        min_success = max(self.window, 5)
        if total_success < min_success:
            self._state = SearchState.EXPLORING
            self._stop_reason = ""
            self._diagnostics = self._build_diagnostics(db, lineage_tree)
            return False, ""

        diagnostics = self._build_diagnostics(db, lineage_tree)
        plateau = bool(diagnostics["s1_plateau"])
        plateau_count = int(diagnostics["plateau_count"])
        activity = diagnostics["s3_activity"] or 0.0

        if plateau and plateau_count >= self.patience and activity >= self.activity_threshold:
            self._state = SearchState.STOPPED
            self._stop_reason = "converged"
            diagnostics["state"] = self._state.value
            diagnostics["stop_reason"] = self._stop_reason
            self._diagnostics = diagnostics
            return True, self._stop_reason

        if plateau:
            self._state = SearchState.WATCHING
        else:
            self._state = SearchState.EXPLORING
        self._stop_reason = ""
        diagnostics["state"] = self._state.value
        diagnostics["stop_reason"] = self._stop_reason
        self._diagnostics = diagnostics
        return False, ""

    def get_strategy_signal(self) -> StrategySignal:
        """Return the selector bias associated with the current state."""
        if self._state == SearchState.WATCHING:
            return StrategySignal(
                tau_multiplier=1.5,
                p_migrate_multiplier=2.0,
                T_insp_multiplier=1.3,
                reason="watching_boost_exploration",
            )
        if self._state == SearchState.STOPPED:
            return StrategySignal(reason="stopped")
        return StrategySignal(reason="normal")

    def get_diagnostics(self) -> dict:
        """Return the most recent diagnostic snapshot."""
        return dict(self._diagnostics)

    def _build_diagnostics(self, db: SuccessDB, lineage_tree: LineageTree) -> dict:
        """Compute human-readable convergence signals from persisted records."""
        records = db.get_all()
        best_history = self._best_fitness_history(records)
        diversity_history = self._diversity_history(records)
        total_success = len(records)
        window = min(self.window, max(total_success // 2, 1))

        improvement = None
        s1_plateau = False
        if len(best_history) > window:
            # Fitness is maximized, so improvement is positive when the latest
            # window improved over the previous best.
            old_best = best_history[-(window + 1)]
            new_best = best_history[-1]
            improvement = (new_best - old_best) / max(abs(old_best), 1e-9)
            s1_plateau = improvement < self.perf_threshold

        diversity_change = None
        s2_trend = "stable"
        if len(diversity_history) > window:
            # Diversity is diagnostic.  The current MVP does not require a
            # declining trend to stop, but it is useful for run summaries.
            diversity_change = diversity_history[-1] - diversity_history[-(window + 1)]
            if diversity_change < -self.diversity_threshold:
                s2_trend = "declining"
            elif diversity_change > self.diversity_threshold:
                s2_trend = "increasing"

        plateau_count = self._consecutive_plateau_count(best_history, window)
        current_diversity = lineage_tree.get_diversity_index()

        return {
            "state": self._state.value,
            "stop_reason": self._stop_reason,
            "plateau_count": plateau_count,
            "patience": self.patience,
            "window": window,
            "s1_fitness_improvement": round(improvement, 6) if improvement is not None else None,
            "s1_fitness_threshold": self.perf_threshold,
            "s1_plateau": s1_plateau,
            "s2_diversity_change": round(diversity_change, 6) if diversity_change is not None else None,
            "s2_diversity_threshold": self.diversity_threshold,
            "s2_trend": s2_trend,
            "s3_activity": round(current_diversity, 4),
            "s3_activity_threshold": self.activity_threshold,
            "current_best_fitness": best_history[-1] if best_history else None,
            "current_diversity": round(current_diversity, 4),
            "total_success_tracked": total_success,
        }

    @staticmethod
    def _best_fitness_history(records: List[SuccessRecord]) -> List[float]:
        """Return the running best fitness after each success record."""
        best = None
        history = []
        for record in records:
            best = record.fitness if best is None else max(best, record.fitness)
            history.append(best)
        return history

    def _diversity_history(self, records: List[SuccessRecord]) -> List[float]:
        """Recompute prefix diversity values for diagnostics.

        This is O(N^2) if called on many records, but the MVP SuccessDB is small
        and this keeps the implementation simple.  A future version can persist
        a checkpointed diversity series if needed.
        """
        history = []
        prefix: List[SuccessRecord] = []
        for record in records:
            prefix.append(record)
            tree = LineageTree()
            tree.rebuild(_RecordsView(prefix))
            history.append(tree.get_diversity_index())
        return history

    def _consecutive_plateau_count(self, best_history: List[float], window: int) -> int:
        """Count consecutive windows whose best-fitness gain is below threshold."""
        count = 0
        for index in range(len(best_history) - 1, window - 1, -1):
            old_best = best_history[index - window]
            new_best = best_history[index]
            improvement = (new_best - old_best) / max(abs(old_best), 1e-9)
            if improvement < self.perf_threshold:
                count += 1
            else:
                break
        return count


class _RecordsView:
    """Minimal adapter so LineageTree can rebuild from a prefix record list."""

    def __init__(self, records: List[SuccessRecord]):
        self._records = list(records)

    def get_all(self) -> List[SuccessRecord]:
        return list(self._records)

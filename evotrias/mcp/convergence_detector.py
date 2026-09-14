"""Deterministic fitness-plateau detection.

The detector observes running-best fitness over a rolling window. A sustained
plateau enters WATCHING so the controller can request meta-guidance. Plateau is
never a stop signal and does not modify parent or inspiration selection.
"""

from __future__ import annotations

from typing import List, Optional

from evotrias.mcp.lineage_tree import LineageTree
from evotrias.db.models import PlateauConfig, SearchState, SuccessRecord
from evotrias.mcp.success_db import SuccessDB


class FitnessPlateauDetector:
    """Maintain EXPLORING or WATCHING from running-best fitness history."""

    def __init__(self, config: PlateauConfig):
        self.window = config.window
        self.improvement_threshold = config.improvement_threshold
        self.patience = config.patience
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
        """Restore persisted state before a controller operation."""
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
        total_success: int,
    ) -> None:
        """Update the current search state from the SuccessDB snapshot."""
        if self._state == SearchState.STOPPED and self._stop_reason:
            self._diagnostics = self._build_diagnostics(db, lineage_tree)
            self._diagnostics["state"] = self._state.value
            self._diagnostics["stop_reason"] = self._stop_reason
            return

        min_success = max(self.window, 5)
        if total_success < min_success:
            self._state = SearchState.EXPLORING
            self._stop_reason = ""
            self._diagnostics = self._build_diagnostics(db, lineage_tree)
            return

        diagnostics = self._build_diagnostics(db, lineage_tree)
        plateau = bool(diagnostics["plateau_detected"])
        plateau_count = int(diagnostics["plateau_count"])
        latest_is_new_best = bool(diagnostics["latest_success_is_new_best"])

        if plateau and not latest_is_new_best and plateau_count >= self.patience:
            self._state = SearchState.WATCHING
        else:
            self._state = SearchState.EXPLORING
        self._stop_reason = ""
        diagnostics["state"] = self._state.value
        diagnostics["stop_reason"] = self._stop_reason
        self._diagnostics = diagnostics

    def mark_stopped(
        self,
        reason: str,
        db: SuccessDB,
        lineage_tree: LineageTree,
    ) -> None:
        """Record a hard-budget stop without treating plateau as convergence."""
        self._state = SearchState.STOPPED
        self._stop_reason = reason
        diagnostics = self._build_diagnostics(db, lineage_tree)
        diagnostics["state"] = self._state.value
        diagnostics["stop_reason"] = reason
        self._diagnostics = diagnostics

    def get_diagnostics(self) -> dict:
        """Return the most recent diagnostic snapshot."""
        return dict(self._diagnostics)

    def _build_diagnostics(self, db: SuccessDB, lineage_tree: LineageTree) -> dict:
        """Compute plateau inputs plus useful current search context."""
        records = db.get_all()
        best_history = self._best_fitness_history(records)
        total_success = len(records)
        window = max(self.window, 1)

        improvement = None
        plateau_detected = False
        if len(best_history) > window:
            old_best = best_history[-(window + 1)]
            new_best = best_history[-1]
            improvement = (new_best - old_best) / max(abs(old_best), 1e-9)
            plateau_detected = improvement <= self.improvement_threshold

        return {
            "state": self._state.value,
            "stop_reason": self._stop_reason,
            "plateau_count": self._consecutive_plateau_count(best_history, window),
            "patience": self.patience,
            "window": window,
            "fitness_improvement": round(improvement, 6) if improvement is not None else None,
            "fitness_improvement_threshold": self.improvement_threshold,
            "plateau_detected": plateau_detected,
            "current_best_fitness": best_history[-1] if best_history else None,
            "current_diversity": round(lineage_tree.get_diversity_index(), 4),
            "total_success_tracked": total_success,
            "latest_success_is_new_best": self._latest_success_is_new_best(records),
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

    def _consecutive_plateau_count(self, best_history: List[float], window: int) -> int:
        """Count consecutive windows whose best-fitness gain is below threshold."""
        count = 0
        for index in range(len(best_history) - 1, window - 1, -1):
            old_best = best_history[index - window]
            new_best = best_history[index]
            improvement = (new_best - old_best) / max(abs(old_best), 1e-9)
            if improvement <= self.improvement_threshold:
                count += 1
            else:
                break
        return count

    @staticmethod
    def _latest_success_is_new_best(records: List[SuccessRecord]) -> bool:
        """Return true when the newest success strictly improved the run best."""
        if len(records) < 2:
            return bool(records)
        previous_best = max(record.fitness for record in records[:-1])
        return records[-1].fitness > previous_best

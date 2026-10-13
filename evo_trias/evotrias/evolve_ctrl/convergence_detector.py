"""Deterministic fitness-plateau detection.

The detector observes running-best fitness over a rolling window. A sustained
plateau enters WATCHING so the controller can request meta-guidance. Plateau is
never a stop signal and does not modify parent or inspiration selection.

The detector is stateless: the previous search state comes in through
``RunState``, the new state goes back out as a return value, and the caller
decides whether to persist it.
"""

from __future__ import annotations

from typing import Any

from evotrias.db.config import PlateauConfig
from evotrias.db.run_state import RunState
from evotrias.enums_and_consts import SearchState
from evotrias.evolve_ctrl.lineage_tree import LineageTree
from evotrias.evolve_ctrl.success_pool import SuccessPool, SuccessRecord


class FitnessPlateauDetector:
    """Derive EXPLORING / WATCHING / STOPPED from running-best fitness history."""

    def __init__(self, config: PlateauConfig):
        self.window = config.window
        self.improvement_threshold = config.improvement_threshold
        self.patience = config.patience

    def evaluate(
        self,
        previous: RunState,
        pool: SuccessPool,
        lineage_tree: LineageTree,
        *,
        stop_reason: str = "",
    ) -> tuple[SearchState, str, dict[str, Any]]:
        """Return ``(state, stop_reason, diagnostics)`` for the current pool.

        A non-empty ``stop_reason`` (budget exhausted) forces STOPPED.  A run
        already STOPPED with a reason stays STOPPED.  Otherwise the state is
        recomputed from scratch; plateau alone never stops a run.
        """
        diagnostics = self._build_diagnostics(pool, lineage_tree)

        if stop_reason:
            state, reason = SearchState.STOPPED, stop_reason
        elif previous.search_state is SearchState.STOPPED and previous.stop_reason:
            state, reason = SearchState.STOPPED, previous.stop_reason
        elif diagnostics["total_success_tracked"] < max(self.window, 5):
            state, reason = SearchState.EXPLORING, ""
        else:
            plateau = bool(diagnostics["plateau_detected"])
            plateau_count = int(diagnostics["plateau_count"])
            latest_is_new_best = bool(diagnostics["latest_success_is_new_best"])
            if plateau and not latest_is_new_best and plateau_count >= self.patience:
                state = SearchState.WATCHING
            else:
                state = SearchState.EXPLORING
            reason = ""

        diagnostics = {"state": state.value, "stop_reason": reason, **diagnostics}
        return state, reason, diagnostics

    def _build_diagnostics(self, pool: SuccessPool, lineage_tree: LineageTree) -> dict[str, Any]:
        """Compute plateau inputs plus useful current search context."""
        records = pool.get_all()
        best_history = self._best_fitness_history(records)
        window = max(self.window, 1)

        improvement = None
        plateau_detected = False
        if len(best_history) > window:
            old_best = best_history[-(window + 1)]
            new_best = best_history[-1]
            improvement = (new_best - old_best) / max(abs(old_best), 1e-9)
            plateau_detected = improvement <= self.improvement_threshold

        return {
            "plateau_count": self._consecutive_plateau_count(best_history, window),
            "patience": self.patience,
            "window": window,
            "fitness_improvement": round(improvement, 6) if improvement is not None else None,
            "fitness_improvement_threshold": self.improvement_threshold,
            "plateau_detected": plateau_detected,
            "current_best_fitness": best_history[-1] if best_history else None,
            "current_diversity": round(lineage_tree.get_diversity_index(), 4),
            "total_success_tracked": len(records),
            "latest_success_is_new_best": self._latest_success_is_new_best(records),
        }

    @staticmethod
    def _best_fitness_history(records: list[SuccessRecord]) -> list[float]:
        """Return the running best fitness after each success record."""
        best = None
        history = []
        for record in records:
            best = record.fitness if best is None else max(best, record.fitness)
            history.append(best)
        return history

    def _consecutive_plateau_count(self, best_history: list[float], window: int) -> int:
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
    def _latest_success_is_new_best(records: list[SuccessRecord]) -> bool:
        """Return true when the newest success strictly improved the run best."""
        if len(records) < 2:
            return bool(records)
        previous_best = max(record.fitness for record in records[:-1])
        return records[-1].fitness > previous_best

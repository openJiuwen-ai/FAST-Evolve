"""Lineage-aware parent selection.

This selector implements a soft-island strategy.  It avoids always selecting
the globally best record, because doing so can collapse the search into one
lineage too early.  Selection is split into two stages:

1. Choose a lineage according to deserved share and exploration deficit.
2. Choose a concrete record inside that lineage with a local UCB score.
"""

from __future__ import annotations

import math
import random
from typing import Dict, Optional

from agent_evolve_mcp.lineage_tree import LineageTree
from agent_evolve_mcp.models import ParentSelectionConfig, StrategySignal, SuccessRecord
from agent_evolve_mcp.success_db import SuccessDB


class LineageAwareParentSelector:
    """Two-stage parent selector using lineage balance plus local UCB."""

    def __init__(self, config: ParentSelectionConfig, lineage_tree: LineageTree):
        self.tau_start = config.tau_start
        self.tau_end = config.tau_end
        self.c_local_start = config.c_local_start
        self.c_local_end = config.c_local_end
        self.p_exploit = config.p_exploit
        self.quality_bonus_strength = config.quality_bonus_strength
        self.elite_rank_fraction = config.elite_rank_fraction
        self.elite_selection_shield = config.elite_selection_shield
        self.lineage_tree = lineage_tree

    def select(
        self,
        db: SuccessDB,
        progress: float,
        signal: Optional[StrategySignal] = None,
    ) -> Optional[SuccessRecord]:
        """Select a parent record and increment its selection count.

        The increment is persisted immediately so parallel generator tasks see
        that this record has already been used.  A future main loop may decide
        whether to roll this count back for failed batches; the MCP MVP keeps
        the simpler optimistic update.
        """
        if db.is_empty():
            return None

        self.lineage_tree.rebuild(db)
        records = db.get_all()
        if len(records) == 1:
            record = records[0]
            db.increment_selection(record.id)
            return record

        tau = self._anneal(self.tau_start, self.tau_end, progress)
        c_local = self._anneal(self.c_local_start, self.c_local_end, progress)
        p_exploit = self.p_exploit

        if signal:
            tau *= signal.tau_multiplier
            if signal.p_exploit_override is not None:
                p_exploit = signal.p_exploit_override

        # Stage 1 chooses which evolutionary branch deserves this attempt.
        lineage_id = self._select_lineage(tau, p_exploit)
        if lineage_id is None:
            best = db.get_best_record()
            if best:
                db.increment_selection(best.id)
            return best

        # Stage 2 chooses the concrete parent within the selected branch.
        record = self._select_within_lineage(db, lineage_id, c_local)
        if record:
            db.increment_selection(record.id)
        return record

    def _select_lineage(self, tau: float, p_exploit: float) -> Optional[str]:
        """Choose a lineage using softmax deserved share and deficit feedback."""
        all_stats = self.lineage_tree.get_all_lineage_stats()
        if not all_stats:
            return None
        if len(all_stats) == 1:
            return next(iter(all_stats))

        # Normalize the task-generic quality signal before applying softmax.
        # The raw fitness scale is benchmark-dependent, while tau is shared
        # across benchmarks.  Min-max normalization keeps the selection
        # policy stable without changing the stored or reported fitness.
        fitnesses = [stats.best_fitness for stats in all_stats.values()]
        min_fitness = min(fitnesses)
        max_fitness = max(fitnesses)
        fitness_range = max_fitness - min_fitness
        potentials = {
            root_id: (
                (stats.best_fitness - min_fitness) / fitness_range
                if fitness_range > 1e-9
                else 0.0
            )
            for root_id, stats in all_stats.items()
        }
        deserved = self._softmax(potentials, tau)

        total_records = sum(stats.size for stats in all_stats.values())
        epsilon = 1.0 / max(total_records, 1)
        # A lineage has deficit > 1 when it has received less exploration than
        # its deserved share.  This is the "soft island" negative feedback.
        deficits = {
            root_id: deserved[root_id] / (stats.share + epsilon)
            for root_id, stats in all_stats.items()
        }

        if random.random() < p_exploit:
            fitnesses = [stats.best_fitness for stats in all_stats.values()]
            min_fitness = min(fitnesses)
            max_fitness = max(fitnesses)
            scores = {}
            for root_id, stats in all_stats.items():
                normalized_quality = (
                    (stats.best_fitness - min_fitness)
                    / (max_fitness - min_fitness + 1e-9)
                )
                quality_bonus = 1.0 + self.quality_bonus_strength * normalized_quality
                scores[root_id] = deficits[root_id] * quality_bonus
            return max(scores, key=scores.get)

        return self._roulette(deficits)

    def _select_within_lineage(
        self,
        db: SuccessDB,
        lineage_id: str,
        c_local: float,
    ) -> Optional[SuccessRecord]:
        """Choose one record within a lineage using rank quality plus UCB."""
        all_records = db.get_all()
        lineage_records = [
            record
            for record in all_records
            if self.lineage_tree.get_lineage_root(record.id) == lineage_id
        ]
        if not lineage_records:
            return None

        # Rank quality is scale-free: it only depends on ordering by fitness.
        sorted_all = sorted(all_records, key=lambda record: record.fitness, reverse=True)
        rank_map = {record.id: index + 1 for index, record in enumerate(sorted_all)}
        total_count = len(sorted_all)
        lineage_selection_count = sum(record.selection_count for record in lineage_records)
        elite_cutoff = max(1, math.ceil(total_count * self.elite_rank_fraction))
        shield = min(max(self.elite_selection_shield, 0.0), 1.0)

        best_score = -float("inf")
        best_record = None
        for record in lineage_records:
            rank = rank_map[record.id]
            quality = (total_count - rank) / max(total_count - 1, 1)
            effective_selection_count = record.selection_count
            if rank <= elite_cutoff:
                effective_selection_count *= 1.0 - shield
            exploration = c_local * math.sqrt(
                math.log(lineage_selection_count + 1) / (effective_selection_count + 1)
            )
            score = quality + exploration
            if score > best_score:
                best_score = score
                best_record = record

        return best_record

    @staticmethod
    def _anneal(start: float, end: float, progress: float) -> float:
        """Exponential annealing from start to end over progress in [0, 1]."""
        progress = min(max(progress, 0.0), 1.0)
        if start <= 0 or end <= 0:
            return end
        return start * (end / start) ** progress

    @staticmethod
    def _softmax(values: Dict[str, float], temperature: float) -> Dict[str, float]:
        """Numerically stable softmax with temperature."""
        max_value = max(values.values())
        temperature = max(temperature, 1e-9)
        exp_values = {
            key: math.exp((value - max_value) / temperature)
            for key, value in values.items()
        }
        total = sum(exp_values.values())
        if total < 1e-15:
            share = 1.0 / len(values)
            return {key: share for key in values}
        return {key: value / total for key, value in exp_values.items()}

    @staticmethod
    def _roulette(weights: Dict[str, float]) -> str:
        """Sample one key proportionally to non-normalized weights."""
        total = sum(weights.values())
        if total < 1e-15:
            return random.choice(list(weights.keys()))
        target = random.random() * total
        cumsum = 0.0
        for key, weight in weights.items():
            cumsum += weight
            if cumsum >= target:
                return key
        return next(reversed(weights))

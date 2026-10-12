"""Lineage-local Boltzmann inspiration selection with migration.

The inspiration selector complements parent selection.  Most inspiration should
come from the same lineage as the parent so a branch can mature coherently.
Some inspiration is sampled from elite records in other lineages to keep useful
ideas moving across the soft islands.
"""

from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Set

from evotrias.db.config import InspirationSelectionConfig
from evotrias.evolve_ctrl.lineage_tree import LineageTree
from evotrias.evolve_ctrl.success_pool import SuccessPool, SuccessRecord


def _inspiration(record: SuccessRecord) -> Dict[str, Any]:
    """Compact shape of one record as sent to generator subagents."""
    return {
        "id": record.id,
        "artifact_ref": record.artifact_ref,
        "sketch": record.sketch,
        "fitness": record.fitness,
        "profile": record.profile,
        "metrics": record.metrics,
        "generation": record.generation,
        "parent_id": record.parent_id,
    }


class LineageBoltzmannInspirationSelector:
    """Select parent-context inspirations for a generator subagent."""

    def __init__(self, config: InspirationSelectionConfig, lineage_tree: LineageTree):
        self.sample_num = config.inspiration_sample_num
        self.p_migrate = config.p_migrate
        self.foreign_elite_num = config.foreign_elite_num
        self.T_start = config.T_start
        self.T_end = config.T_end
        self.lineage_tree = lineage_tree

    def select(
        self,
        parent: SuccessRecord,
        db: SuccessPool,
        progress: float,
        sample_num: Optional[int] = None,
    ) -> List[dict]:
        """Return inspiration dictionaries, always including the parent first."""
        parent_inspiration = _inspiration(parent)
        parent_inspiration["is_parent"] = True
        parent_inspiration["is_migration"] = False
        result = [parent_inspiration]

        all_records = db.get_all()
        if len(all_records) <= 1:
            return result

        T = self._anneal(self.T_start, self.T_end, progress)
        p_migrate = self.p_migrate

        # Local pool preserves lineage direction; foreign elites implement
        # controlled migration across branches.
        parent_lineage = self.lineage_tree.get_lineage_root(parent.id)
        local_pool = [
            record
            for record in all_records
            if record.id != parent.id and self.lineage_tree.get_lineage_root(record.id) == parent_lineage
        ]
        foreign_elites = self._build_foreign_elites(all_records, parent.id, parent_lineage)

        selected: List[SuccessRecord] = []
        selected_ids: Set[str] = set()
        target_count = self.sample_num if sample_num is None else max(sample_num, 0)

        # Each inspiration slot independently decides whether to migrate.
        for _ in range(target_count):
            inspiration = None
            if (
                random.random() < p_migrate
                and foreign_elites
                and any(record.id not in selected_ids for record in foreign_elites)
            ):
                available = [record for record in foreign_elites if record.id not in selected_ids]
                inspiration = random.choice(available)
            else:
                available = [record for record in local_pool if record.id not in selected_ids]
                if available:
                    inspiration = self._boltzmann_sample(available, T)
                else:
                    fallback = [
                        record for record in all_records if record.id != parent.id and record.id not in selected_ids
                    ]
                    if fallback:
                        inspiration = self._boltzmann_sample(fallback, T)

            if inspiration:
                selected.append(inspiration)
                selected_ids.add(inspiration.id)

        # Present local high-quality examples before migrated examples.  This
        # keeps the parent lineage visually prominent in the generator prompt.
        local_selected = [
            record for record in selected if self.lineage_tree.get_lineage_root(record.id) == parent_lineage
        ]
        foreign_selected = [
            record for record in selected if self.lineage_tree.get_lineage_root(record.id) != parent_lineage
        ]
        local_selected.sort(key=lambda record: record.fitness, reverse=True)
        foreign_selected.sort(key=lambda record: record.fitness, reverse=True)

        for record in local_selected + foreign_selected:
            inspiration = _inspiration(record)
            inspiration["is_parent"] = False
            inspiration["is_migration"] = self.lineage_tree.get_lineage_root(record.id) != parent_lineage
            result.append(inspiration)

        return result

    def _build_foreign_elites(
        self,
        all_records: List[SuccessRecord],
        parent_id: str,
        parent_lineage: str,
    ) -> List[SuccessRecord]:
        """Collect top records from every non-parent lineage."""
        by_lineage = {}
        for record in all_records:
            if record.id == parent_id:
                continue
            root = self.lineage_tree.get_lineage_root(record.id)
            if root != parent_lineage:
                by_lineage.setdefault(root, []).append(record)

        elites = []
        for records in by_lineage.values():
            elites.extend(sorted(records, key=lambda record: record.fitness, reverse=True)[: self.foreign_elite_num])
        return elites

    @staticmethod
    def _boltzmann_sample(candidates: List[SuccessRecord], T: float) -> SuccessRecord:
        """Sample by fitness with Boltzmann/Gibbs probabilities."""
        if len(candidates) == 1:
            return candidates[0]

        fitnesses = [record.fitness for record in candidates]
        min_fitness = min(fitnesses)
        max_fitness = max(fitnesses)
        fitness_range = max_fitness - min_fitness
        normalized_fitnesses = [
            (fitness - min_fitness) / fitness_range if fitness_range > 1e-9 else 0.0 for fitness in fitnesses
        ]
        max_normalized_fitness = max(normalized_fitnesses)
        T = max(T, 1e-9)
        exp_values = [math.exp((fitness - max_normalized_fitness) / T) for fitness in normalized_fitnesses]
        total = sum(exp_values)
        if total < 1e-15:
            return random.choice(candidates)

        target = random.random()
        cumsum = 0.0
        for index, value in enumerate(exp_values):
            cumsum += value / total
            if cumsum >= target:
                return candidates[index]
        return candidates[-1]

    @staticmethod
    def _anneal(start: float, end: float, progress: float) -> float:
        """Exponential annealing from start to end over progress in [0, 1]."""
        progress = min(max(progress, 0.0), 1.0)
        if start <= 0 or end <= 0:
            return end
        return start * (end / start) ** progress

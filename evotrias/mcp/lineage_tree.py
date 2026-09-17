"""Lineage forest rebuilt from SuccessPool parent links.

The controller does not persist a separate graph.  It rebuilds this lightweight
forest from ``SuccessRecord.parent_id`` whenever it needs to make a decision.
Each root represents an initial successful candidate or a record whose parent
is not present in the current SuccessPool.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from evotrias.mcp.success_pool import SuccessPool, SuccessRecord


@dataclass
class LineageStats:
    """Aggregate statistics for one lineage root."""

    root_id: str
    size: int = 0
    best_fitness: float = float("-inf")
    best_record_id: str = ""
    max_depth: int = 0
    share: float = 0.0
    total_selections: int = 0
    recent_improvement: float = 0.0


class LineageTree:
    """In-memory lineage index used by all three controller solvers."""

    def __init__(self):
        self._roots: List[str] = []
        self._children: Dict[str, List[str]] = {}
        self._root_of: Dict[str, str] = {}
        self._depth_of: Dict[str, int] = {}
        self._lineage_stats: Dict[str, LineageStats] = {}
        self._record_map: Dict[str, SuccessRecord] = {}

    def rebuild(self, db: SuccessPool) -> None:
        """Rebuild roots, child edges, lineage ownership, depth, and stats."""
        self._roots.clear()
        self._children.clear()
        self._root_of.clear()
        self._depth_of.clear()
        self._lineage_stats.clear()
        self._record_map.clear()

        records = db.get_all()
        if not records:
            return

        record_ids = {record.id for record in records}
        self._record_map = {record.id: record for record in records}

        # Roots are records without a known parent in this SuccessPool snapshot.
        for record in records:
            if record.parent_id is None or record.parent_id not in record_ids:
                self._roots.append(record.id)
            else:
                self._children.setdefault(record.parent_id, []).append(record.id)

        # Breadth-first traversal assigns every reachable record to one root.
        for root_id in self._roots:
            self._root_of[root_id] = root_id
            self._depth_of[root_id] = 0
            queue = deque([root_id])
            while queue:
                node_id = queue.popleft()
                for child_id in self._children.get(node_id, []):
                    self._root_of[child_id] = root_id
                    self._depth_of[child_id] = self._depth_of[node_id] + 1
                    queue.append(child_id)

        self._compute_lineage_stats()

    def get_lineage_root(self, record_id: str) -> str:
        """Return the root id for a record, falling back to the record itself."""
        return self._root_of.get(record_id, record_id)

    def get_descendants(self, record_id: str) -> Set[str]:
        """Return all descendants of a record by DFS."""
        descendants = set()
        stack = list(self._children.get(record_id, []))
        while stack:
            node_id = stack.pop()
            descendants.add(node_id)
            stack.extend(self._children.get(node_id, []))
        return descendants

    def get_lineage_stats(self, root_id: str) -> Optional[LineageStats]:
        """Return aggregate stats for one lineage root."""
        return self._lineage_stats.get(root_id)

    def get_all_lineage_stats(self) -> Dict[str, LineageStats]:
        """Return a copy of all lineage stats."""
        return dict(self._lineage_stats)

    def get_diversity_index(self) -> float:
        """Compute normalized Shannon entropy over lineage shares.

        A value near 0 means the success pool has collapsed into one lineage.
        A value near 1 means records are evenly distributed across lineages.
        The value is a monitoring signal, not an optimization objective.
        """
        lineage_count = len(self._roots)
        if lineage_count <= 1:
            return 0.0

        entropy = 0.0
        for stats in self._lineage_stats.values():
            if stats.share > 0:
                entropy -= stats.share * math.log(stats.share)
        max_entropy = math.log(lineage_count)
        if max_entropy < 1e-9:
            return 0.0
        return entropy / max_entropy

    def get_lineage_depth(self, record_id: str) -> int:
        """Return depth under the lineage root, with roots at depth 0."""
        return self._depth_of.get(record_id, 0)

    def get_lineage_records(self, root_id: str) -> List[SuccessRecord]:
        """Return all records that belong to a lineage."""
        return [
            self._record_map[record_id]
            for record_id, lineage_root in self._root_of.items()
            if lineage_root == root_id and record_id in self._record_map
        ]

    @property
    def roots(self) -> List[str]:
        return list(self._roots)

    @property
    def num_lineages(self) -> int:
        return len(self._roots)

    def _compute_lineage_stats(self) -> None:
        """Compute size, best fitness, depth, share, and selection totals."""
        total_records = len(self._record_map)
        lineage_records: Dict[str, List[SuccessRecord]] = {}
        for record_id, root_id in self._root_of.items():
            record = self._record_map.get(record_id)
            if record:
                lineage_records.setdefault(root_id, []).append(record)

        for root_id, records in lineage_records.items():
            best = max(records, key=lambda record: record.fitness)
            self._lineage_stats[root_id] = LineageStats(
                root_id=root_id,
                size=len(records),
                best_fitness=best.fitness,
                best_record_id=best.id,
                max_depth=max(self._depth_of.get(record.id, 0) for record in records),
                share=len(records) / max(total_records, 1),
                total_selections=sum(record.selection_count for record in records),
                recent_improvement=self._compute_recent_improvement(records),
            )

    @staticmethod
    def _compute_recent_improvement(records: List[SuccessRecord]) -> float:
        """Estimate recent lineage improvement between latest two generations."""
        if len(records) < 2:
            return 0.0

        max_generation = max(record.generation for record in records)
        if max_generation == 0:
            return 0.0

        current = [record for record in records if record.generation == max_generation]
        previous = [record for record in records if record.generation == max_generation - 1]
        if not current or not previous:
            return 0.0

        current_best = max(record.fitness for record in current)
        previous_best = max(record.fitness for record in previous)
        denominator = max(abs(previous_best), 1e-9)
        return (current_best - previous_best) / denominator

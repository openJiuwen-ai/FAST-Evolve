# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Per-run configuration, persisted as ``config.json``.

Written once by ``configure_run`` (caller arguments plus environment
overrides, frozen for the run) and read by every controller construction.
Everything is a default today; per-run customisation lands through the
``configure_run`` interface later.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from evotrias.db import paths
from evotrias.db.base import DBObject


@dataclass
class ParentSelectionConfig:
    """Configuration for the lineage-aware parent selector.

    The selector first chooses a lineage using a soft-island policy, then picks
    a concrete record inside that lineage using local UCB.  The ``*_start`` and
    ``*_end`` values are annealed by search progress in ``[0, 1]``.
    """

    tau_start: float = 1.5
    tau_end: float = 0.5
    c_local_start: float = 1.5
    c_local_end: float = 0.5
    p_exploit: float = 0.7
    quality_bonus_strength: float = 0.3
    elite_rank_fraction: float = 0.15
    elite_selection_shield: float = 0.6


@dataclass
class InspirationSelectionConfig:
    """Configuration for Boltzmann inspiration sampling with migration."""

    inspiration_sample_num: int = 3
    p_migrate: float = 0.2
    foreign_elite_num: int = 2
    T_start: float = 1.5
    T_end: float = 0.5


@dataclass
class RunBudgetConfig:
    """Hard run limits that stop task replenishment."""

    max_total_tasks: int = 100
    max_time_seconds: float | None = None


@dataclass
class SchedulerConfig:
    """Task-pool shape confirmed at configure_run time.

    ``lineage_root_number`` is the number of initial exploration directions
    and therefore the number of lineage roots the run establishes.  It bounds
    the root phase: at most this many tasks are minted before all of them must
    finish (the root barrier).  ``max_concurrent_tasks`` is the in-flight task
    target that holds in every phase, root phase included.  The two are
    deliberately independent knobs.
    """

    lineage_root_number: int = 4
    max_concurrent_tasks: int = 4


@dataclass
class PlateauConfig:
    """Configuration for fitness-plateau detection."""

    window: int = 5
    improvement_threshold: float = 0.0
    patience: int = 4


@dataclass
class MetaEvolveConfig:
    """Configuration for lightweight meta-guidance control."""

    enabled: bool = True
    guidance_num_to_generate: int = 3
    guidance_max_uses: int = 2


@dataclass
class NotesConfig:
    """Configuration for run-local Notes: when to request them and how to select them.

    Rounds between ``active_start_round`` and ``active_end_round`` anneal the
    quantile and best-ratio thresholds linearly from ``*_start`` to ``*_end``.
    """

    enabled: bool = True
    warmup_tasks: int = 10
    active_start_round: int = 11
    active_end_round: int = 50
    delta_quantile_start: float = 0.80
    delta_quantile_end: float = 0.60
    delta_threshold_min: float = 0.02
    delta_threshold_max: float = 0.05
    positive_best_ratio_start: float = 0.80
    positive_best_ratio_end: float = 1.00
    pos_neg_target: int = 5
    error_prompt_limit: int = 10


class Config(DBObject):
    """Top-level run configuration grouped by solver.

    Only ``configure_run`` writes this file, so a non-empty ``created_at`` is
    how a second ``configure_run`` for the same run is recognised.
    """

    parent_selection: ParentSelectionConfig = field(default_factory=ParentSelectionConfig)
    inspiration: InspirationSelectionConfig = field(default_factory=InspirationSelectionConfig)
    budget: RunBudgetConfig = field(default_factory=RunBudgetConfig)
    plateau: PlateauConfig = field(default_factory=PlateauConfig)
    meta_evolve: MetaEvolveConfig = field(default_factory=MetaEvolveConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    notes: NotesConfig = field(default_factory=NotesConfig)

    def __init__(self, run_id: str):
        super().__init__(paths.run_dir(run_id) / paths.CONFIG)

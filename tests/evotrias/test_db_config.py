# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Tests for evotrias.db.config."""

from __future__ import annotations

import dataclasses
import json

import pytest

from evotrias.db import paths
from evotrias.db.config import (
    Config,
    InspirationSelectionConfig,
    MetaEvolveConfig,
    NotesConfig,
    ParentSelectionConfig,
    PlateauConfig,
    RunBudgetConfig,
    SchedulerConfig,
)

RUN_ID = "cfg_test"


@pytest.fixture(autouse=True)
def cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_missing_file_yields_all_defaults_and_unconfigured():
    cfg = Config(RUN_ID)
    assert cfg.created_at == ""
    assert cfg.parent_selection == ParentSelectionConfig()
    assert cfg.inspiration == InspirationSelectionConfig()
    assert cfg.budget == RunBudgetConfig()
    assert cfg.plateau == PlateauConfig()
    assert cfg.meta_evolve == MetaEvolveConfig()
    assert cfg.scheduler == SchedulerConfig()
    assert cfg.notes == NotesConfig()
    assert not (paths.run_dir(RUN_ID) / paths.CONFIG).exists()


def test_with_block_writes_config_json_and_reloads_typed():
    with Config(RUN_ID) as cfg:
        cfg.budget.max_total_tasks = 30
        cfg.scheduler.lineage_root_number = 6
        cfg.notes.enabled = False
    path = paths.run_dir(RUN_ID) / paths.CONFIG
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["budget"]["max_total_tasks"] == 30
    assert on_disk["scheduler"]["lineage_root_number"] == 6
    assert on_disk["notes"]["enabled"] is False

    reloaded = Config(RUN_ID)
    assert reloaded.created_at == on_disk["created_at"] != ""
    assert isinstance(reloaded.budget, RunBudgetConfig)
    assert isinstance(reloaded.notes, NotesConfig)
    assert reloaded.budget.max_total_tasks == 30
    assert reloaded.scheduler.lineage_root_number == 6
    assert reloaded.notes.enabled is False


def test_unknown_keys_on_disk_are_ignored():
    path = paths.run_dir(RUN_ID) / paths.CONFIG
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"budget": {"max_total_tasks": 7, "legacy": 1}, "retired": {}}),
        encoding="utf-8",
    )
    cfg = Config(RUN_ID)
    assert cfg.budget.max_total_tasks == 7
    assert not hasattr(cfg, "retired")


def test_notes_defaults_match_former_module_constants():
    n = NotesConfig()
    assert (n.enabled, n.warmup_tasks, n.active_start_round, n.active_end_round) == (True, 10, 11, 50)
    assert (n.delta_quantile_start, n.delta_quantile_end) == (0.80, 0.60)
    assert (n.delta_threshold_min, n.delta_threshold_max) == (0.02, 0.05)
    assert (n.positive_best_ratio_start, n.positive_best_ratio_end) == (0.80, 1.00)
    assert (n.pos_neg_target, n.error_prompt_limit) == (5, 10)


def test_every_section_is_a_dataclass_with_defaults():
    for f in dataclasses.fields(Config):
        section = f.default_factory()
        assert dataclasses.is_dataclass(section), f.name
        for sub in dataclasses.fields(section):
            has_default = sub.default is not dataclasses.MISSING or sub.default_factory is not dataclasses.MISSING
            assert has_default, (f.name, sub.name)

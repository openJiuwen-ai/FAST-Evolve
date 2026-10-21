# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Tests for evotrias.db.task_contexts."""

from __future__ import annotations

import json

import pytest

from evotrias.db import paths
from evotrias.db.task_contexts import TaskContext, TaskContexts

RUN_ID = "ctx_test"


@pytest.fixture(autouse=True)
def cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_missing_file_is_empty():
    contexts = TaskContexts(RUN_ID)
    assert len(contexts) == 0
    assert contexts.get("task_001") is None
    assert not (paths.run_dir(RUN_ID) / paths.TASK_CONTEXTS).exists()


def test_get_create_adds_in_memory_and_with_persists_typed():
    with TaskContexts(RUN_ID) as contexts:
        context = contexts.get("task_001", create=True)
        assert isinstance(context, TaskContext)
        context.parent_id = "task_000"
        context.parent_selected = True
        assert contexts.get("task_001", create=True) is context
    reloaded = TaskContexts(RUN_ID)
    assert len(reloaded) == 1
    got = reloaded.get("task_001")
    assert isinstance(got, TaskContext)
    assert (got.task_id, got.parent_id, got.parent_selected) == ("task_001", "task_000", True)
    assert got.created_at


def test_get_create_without_with_never_writes():
    contexts = TaskContexts(RUN_ID)
    contexts.get("task_001", create=True)
    assert not (paths.run_dir(RUN_ID) / paths.TASK_CONTEXTS).exists()


def test_on_disk_shape_wraps_tasks():
    with TaskContexts(RUN_ID) as contexts:
        contexts.get("task_002", create=True).inspiration_ids = ["task_001"]
    on_disk = json.loads((paths.run_dir(RUN_ID) / paths.TASK_CONTEXTS).read_text(encoding="utf-8"))
    assert set(on_disk) == {"tasks", "created_at", "updated_at"}
    assert set(on_disk["tasks"]) == {"task_002"}
    assert on_disk["tasks"]["task_002"]["inspiration_ids"] == ["task_001"]
    assert "updated_at" not in on_disk["tasks"]["task_002"]

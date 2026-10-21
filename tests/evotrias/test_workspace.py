# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Tests for evotrias.setup.workspace: reading and writing the workspace's harness binding."""

from __future__ import annotations

from evotrias.setup.workspace import ACTIVE_HARNESS, read_active_harness, write_active_harness


def test_not_a_workspace(tmp_path):
    assert read_active_harness(tmp_path) is None


def test_write_then_read_back(tmp_path):
    write_active_harness(tmp_path, "opencode")

    assert (tmp_path / ACTIVE_HARNESS).read_bytes() == b"opencode\n"
    assert read_active_harness(tmp_path) == "opencode"


def test_read_ignores_surrounding_whitespace(tmp_path):
    (tmp_path / ACTIVE_HARNESS).parent.mkdir()
    (tmp_path / ACTIVE_HARNESS).write_text("  claude\r\n", encoding="utf-8")

    assert read_active_harness(tmp_path) == "claude"

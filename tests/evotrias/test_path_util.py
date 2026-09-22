"""Tests for evotrias.utils.path_util."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from evotrias.utils.path_util import resolve_path


@pytest.fixture(autouse=True)
def cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_relative_path_resolves_against_cwd(cwd):
    assert resolve_path("runs/run-X/a.txt") == resolve_path(cwd / "runs" / "run-X" / "a.txt")


def test_relative_and_absolute_spellings_of_one_file_are_equal(cwd):
    assert resolve_path("runs/run-X/a.txt") == resolve_path(str(cwd / "runs" / "run-X" / "a.txt"))


def test_dotdot_is_collapsed(cwd):
    assert resolve_path("runs/run-X/tasks/../../other.txt") == resolve_path(cwd / "runs" / "other.txt")


def test_result_is_absolute_and_dotdot_free():
    resolved = resolve_path("a/../b/./c.txt")
    assert resolved.is_absolute()
    assert ".." not in resolved.parts and "." not in resolved.parts


def test_file_need_not_exist(cwd):
    assert not (cwd / "missing.txt").exists()
    assert resolve_path("missing.txt") == resolve_path(cwd / "missing.txt")


def test_accepts_path_objects(cwd):
    assert resolve_path(Path("runs") / "x.txt") == resolve_path("runs/x.txt")


@pytest.mark.skipif(os.name != "nt", reason="case folding only applies on Windows")
def test_case_is_folded_on_windows():
    assert resolve_path("RUNS/Run-X/A.TXT") == resolve_path("runs/run-x/a.txt")


@pytest.mark.skipif(os.name == "nt", reason="posix filesystems are case-sensitive")
def test_case_is_preserved_on_posix():
    assert resolve_path("RUNS/A.TXT") != resolve_path("runs/a.txt")

"""Tests for evotrias.db.paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from evotrias.db import paths

_ROOTS = [paths.DB_ROOT, paths.LOCKS_ROOT, paths.RUNS_ROOT]
_GLOBAL_FILES = [paths.SESSION_MAP, paths.OPENCODE_REGISTRY]
_LOCKS = [paths.HOOK_QUEUE_LOCK, paths.LOGS_LOCK]
_RUN_FILES = [
    paths.RUN_STATE,
    paths.TASK_CONTEXTS,
    paths.SUCCESS_DB,
    paths.ATTEMPTS,
    paths.NOTES,
    paths.NOTE_REQUESTS,
    paths.NOTES_EVENTS,
    paths.META_GUIDANCE_STATE,
    paths.META_GUIDANCE_USES,
    paths.TASK_POOL,
    paths.AGENT_INDEX,
    paths.RUN_SUMMARY_MD,
    paths.RUN_SUMMARY_JSON,
]


@pytest.fixture(autouse=True)
def cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_roots_are_relative_and_resolve_under_cwd(cwd):
    for root in _ROOTS:
        assert not root.is_absolute()
        assert root.resolve().is_relative_to(cwd)


def test_global_files_and_locks_sit_under_their_roots():
    for path in _GLOBAL_FILES:
        assert path.parent == paths.DB_ROOT
        assert path.suffix == ".json"
    for path in _LOCKS:
        assert path.parent == paths.LOCKS_ROOT
        assert path.suffix == ".lock"


def test_run_file_constants_are_bare_names_with_known_suffixes():
    for name in _RUN_FILES:
        assert "/" not in name and "\\" not in name
        assert Path(name).suffix in {".json", ".jsonl", ".md"}
    assert len(set(_RUN_FILES)) == len(_RUN_FILES)


def test_run_dirs_for_plain_id():
    assert paths.run_dir("abc") == paths.DB_ROOT / "runs" / "abc"
    assert paths.public_run_dir("abc") == paths.RUNS_ROOT / "abc"


@pytest.mark.parametrize(
    "raw, safe",
    [
        ("circle_packing_20260901155851", "circle_packing_20260901155851"),
        ("a/b c!", "a_b_c_"),
        ("  padded  ", "padded"),
        ("   ", "default"),
        ("", "default"),
    ],
)
def test_safe_name(raw, safe):
    assert paths._safe_name(raw) == safe


def test_both_run_dirs_share_the_sanitized_name():
    assert paths.run_dir("x/y").name == paths.public_run_dir("x/y").name == "x_y"


def test_paths_follow_cwd_changes(monkeypatch, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    before = paths.DB_ROOT.resolve()
    monkeypatch.chdir(other)
    after = paths.DB_ROOT.resolve()
    assert before == tmp_path / ".evotrias" / "db"
    assert after == other / ".evotrias" / "db"

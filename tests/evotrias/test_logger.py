from __future__ import annotations

import builtins
import logging
import os
import tarfile
from datetime import UTC, datetime

import pytest

from evotrias import logger as logger_mod
from evotrias.logger import _Logger


def _read(path) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def tmp_log(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.chdir(repo_root)
    log_dir = repo_root / ".evotrias" / "logs"
    # Must stay well above one formatted line so a single emit never immediately
    # rotates latest.log away, yet small enough that a handful of lines still
    # triggers rotation.
    monkeypatch.setattr(logger_mod, "_ROTATE_BYTES", 300)
    monkeypatch.setenv("EVOTRIAS_LOG_LEVEL", "DEBUG")
    fresh = _Logger()
    return log_dir, fresh


def test_writes_to_latest_log(tmp_log):
    log_dir, log = tmp_log
    log.info("hello world")
    assert _read(log_dir / "latest.log").strip().endswith("hello world")


def test_level_filtering(monkeypatch, tmp_log):
    log_dir, log = tmp_log
    monkeypatch.setenv("EVOTRIAS_LOG_LEVEL", "WARNING")
    log._log.setLevel(logging.WARNING)
    log.info("should-not-appear")
    log.warning("should-appear")
    content = _read(log_dir / "latest.log")
    assert "should-not-appear" not in content
    assert "should-appear" in content


def test_caller_location_captured(tmp_log):
    log_dir, log = tmp_log
    log.info("locate me")
    assert os.path.basename(__file__) in _read(log_dir / "latest.log")


def test_rotation_renames_and_tars(tmp_log):
    log_dir, log = tmp_log
    for i in range(20):
        log.info(f"line {i:04d} padding padding padding padding")
    archives = [p for p in log_dir.iterdir() if p.name.endswith(".tar.gz")]
    assert archives, "expected at least one rotated .tar.gz archive"
    for arc in archives:
        with tarfile.open(arc, "r:gz") as tar:
            names = tar.getnames()
            assert len(names) == 1
            member = tar.extractfile(names[0])
            assert member is not None
            assert b"padding" in member.read()


def test_tar_failure_leaves_log_for_next_round(monkeypatch, tmp_log):
    log_dir, log = tmp_log

    def _boom(name, *a, **k):
        raise OSError("simulated tar failure")

    monkeypatch.setattr(tarfile, "open", _boom)
    for i in range(20):
        log.info(f"line {i:04d} padding padding padding padding")
    leftover = [p for p in log_dir.iterdir() if p.name.endswith(".log") and p.name != "latest.log"]
    assert leftover, "expected [ts].log to survive a tar failure"


def test_repeated_rotations_do_not_leak_lock_fd(tmp_log):
    # The lock fd is scoped to the `with blocking_filelock(...)` block: every
    # emit opens and closes its own descriptor. Many rotations in a row must
    # not reuse a stale/closed fd (which would raise EBADF out of emit).
    log_dir, log = tmp_log
    for i in range(120):
        log.info(f"line {i:04d} padding padding padding padding padding")
    assert [p for p in log_dir.iterdir() if p.name.endswith(".tar.gz")]
    log.info("still alive after all those rotations")
    assert "still alive" in _read(log_dir / "latest.log")


def test_rename_failure_neither_raises_nor_loses_lines(monkeypatch, tmp_log):
    # Regression for the 20260818 incident: os.rename blocked by another
    # process's handle (WinError 32) must not escape a logging call; every
    # line must survive in latest.log and the failure must leave an ERROR
    # breadcrumb (written directly, bypassing the logging machinery).
    log_dir, log = tmp_log

    def _busy(src, dst):
        raise PermissionError(32, "another process is using this file")

    monkeypatch.setattr(os, "rename", _busy)
    for i in range(20):
        log.info(f"line {i:04d} padding padding padding padding")
    content = _read(log_dir / "latest.log")
    payload_lines = [l for l in content.splitlines() if " INFO " in l and "padding" in l]
    assert len(payload_lines) == 20
    assert "PermissionError" in content, "expected a breadcrumb for the failed rotation"
    assert (log_dir / "latest.log").stat().st_size > logger_mod._ROTATE_BYTES
    assert not [p for p in log_dir.iterdir() if p.name.endswith(".tar.gz")]


def test_emit_never_raises_even_when_open_fails(monkeypatch, tmp_log):
    # The outer safety net: no failure inside emit may crash the caller.
    _, log = tmp_log

    def _boom(*args, **kwargs):
        raise OSError("simulated open failure")

    monkeypatch.setattr(builtins, "open", _boom)
    log.info("swallowed")  # must not raise


def _freeze_clock(monkeypatch):
    """Pin utcnow so every rotation in a test picks the same [ts] stem."""
    fixed = datetime(2026, 8, 11, 12, 0, 0, tzinfo=UTC)

    class _FrozenDatetime:
        @staticmethod
        def now(tz=None):
            return fixed

    monkeypatch.setattr(logger_mod, "datetime", _FrozenDatetime)


def test_same_second_rotations_do_not_clobber_each_other(monkeypatch, tmp_log):
    # A successful rotation tars [ts].log and removes it, so probing for the
    # bare .log can never see the previous archive: the counter would stay 0
    # and `tarfile.open(..., "w:gz")` would overwrite it. Probe the .tar.gz.
    log_dir, log = tmp_log
    _freeze_clock(monkeypatch)

    for i in range(20):
        log.info(f"line {i:04d} padding padding padding padding padding")

    archives = sorted(p for p in log_dir.iterdir() if p.name.endswith(".tar.gz"))
    assert len(archives) > 1, "20 lines at a 100-byte threshold must rotate repeatedly"

    archived_lines = 0
    for arc in archives:
        with tarfile.open(arc, "r:gz") as tar:
            for name in tar.getnames():
                member = tar.extractfile(name)
                assert member is not None
                archived_lines += member.read().count(b"line ")

    live = log_dir / "latest.log"
    live_lines = live.read_bytes().count(b"line ") if live.exists() else 0
    assert archived_lines + live_lines == 20, "no line may be lost to a clobbered archive"


def test_same_second_rotation_skips_a_leftover_log_from_a_failed_tar(monkeypatch, tmp_log):
    # The other half of the collision guard: when tar failed earlier, [ts].log
    # survives on disk and os.rename would silently replace it.
    log_dir, log = tmp_log
    _freeze_clock(monkeypatch)

    stale = log_dir / "20260811T120000.log"
    log_dir.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"earlier round that never got tarred\n")

    for i in range(20):
        log.info(f"line {i:04d} padding padding padding padding padding")

    assert (
        stale.read_bytes() == b"earlier round that never got tarred\n"
    ), "a leftover [ts].log must not be overwritten by os.rename"

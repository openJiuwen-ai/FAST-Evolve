# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
from __future__ import annotations

import threading
import time

import pytest

from evotrias.utils.filelock import blocking_filelock, filelock


def test_acquires_and_yields_true(tmp_path):
    lock_path = tmp_path / "a.lock"
    with filelock(lock_path) as held:
        assert held is True
    assert lock_path.exists(), "sentinel file stays on disk after release"


def test_creates_missing_parent_directory(tmp_path):
    lock_path = tmp_path / "deep" / "nested" / "b.lock"
    assert not lock_path.parent.exists()
    with filelock(lock_path) as held:
        assert held
    assert lock_path.parent.is_dir()


def test_accepts_str_and_pathlike(tmp_path):
    lock_path = tmp_path / "c.lock"
    with filelock(str(lock_path)) as held:
        assert held
    with filelock(lock_path) as held:
        assert held


def test_bare_filename_without_parent_dir(tmp_path, monkeypatch):
    # dirname("x.lock") == "" — makedirs must be skipped, not called with "".
    monkeypatch.chdir(tmp_path)
    with filelock("x.lock") as held:
        assert held
    assert (tmp_path / "x.lock").exists()


def test_releases_on_exception(tmp_path):
    # An exception inside the guarded block must still release+close the fd,
    # so the lock can be acquired again afterwards.
    lock_path = tmp_path / "d.lock"

    with pytest.raises(RuntimeError):
        with filelock(lock_path) as held:
            assert held
            raise RuntimeError("boom")

    with filelock(lock_path) as held:
        assert held, "lock must be re-acquirable after an exception"


def test_repeated_cycles_do_not_leak_fds(tmp_path):
    # Each cycle opens and closes its own descriptor; a stale/closed fd would
    # surface as EBADF (or an fd-exhaustion OSError) on a later iteration.
    lock_path = tmp_path / "e.lock"
    for _ in range(200):
        with filelock(lock_path) as held:
            assert held


def test_yields_false_when_already_held(tmp_path):
    # Same-process reentry: the second acquisition uses a distinct fd, so on
    # POSIX flock this is a genuine conflict. Windows msvcrt.locking also
    # rejects an overlapping byte range from a second descriptor.
    lock_path = tmp_path / "f.lock"
    with filelock(lock_path) as outer:
        assert outer
        with filelock(lock_path) as inner:
            assert inner is False, "a contended lock must yield False, not block"
    with filelock(lock_path) as again:
        assert again, "lock is free once the outer block exits"


def test_best_effort_when_no_os_lock_available(tmp_path, monkeypatch):
    import evotrias.utils.filelock as fl

    monkeypatch.setattr(fl, "_lock_ops", lambda: None)
    lock_path = tmp_path / "g.lock"
    with filelock(lock_path) as held:
        assert held is True, "no OS lock primitive -> proceed unguarded"
    assert not lock_path.exists(), "no sentinel file is opened in best-effort mode"


def test_lock_path_is_a_parameter_not_a_constant(tmp_path):
    # Two different paths are independent locks.
    first, second = tmp_path / "one.lock", tmp_path / "two.lock"
    with filelock(first) as a:
        assert a
        with filelock(second) as b:
            assert b, "a different path must not contend with the first lock"


def test_blocking_acquires_when_free(tmp_path):
    lock_path = tmp_path / "h.lock"
    with blocking_filelock(lock_path) as held:
        assert held is True
    assert lock_path.exists()


def test_blocking_times_out_when_held(tmp_path):
    lock_path = tmp_path / "i.lock"
    with filelock(lock_path) as outer:
        assert outer
        start = time.monotonic()
        with blocking_filelock(lock_path, timeout=0.2) as blocked:
            assert blocked is False, "a still-held lock must yield False after timeout"
        assert time.monotonic() - start >= 0.2


def test_blocking_waits_then_acquires(tmp_path):
    lock_path = tmp_path / "j.lock"
    released_at = []

    def hold_briefly():
        with filelock(lock_path) as held:
            assert held
            time.sleep(0.2)
            released_at.append(time.monotonic())

    holder = threading.Thread(target=hold_briefly)
    holder.start()
    time.sleep(0.05)  # let the holder win the race for the lock
    with blocking_filelock(lock_path, timeout=5.0) as held:
        assert held is True, "must acquire once the holder releases"
        assert time.monotonic() >= released_at[0]
    holder.join()


def test_blocking_releases_on_exception(tmp_path):
    lock_path = tmp_path / "k.lock"
    with pytest.raises(RuntimeError):
        with blocking_filelock(lock_path) as held:
            assert held
            raise RuntimeError("boom")
    with filelock(lock_path) as held:
        assert held, "lock must be re-acquirable after an exception"


def test_blocking_best_effort_when_no_os_lock_available(tmp_path, monkeypatch):
    import evotrias.utils.filelock as fl

    monkeypatch.setattr(fl, "_lock_ops", lambda: None)
    lock_path = tmp_path / "l.lock"
    with blocking_filelock(lock_path) as held:
        assert held is True, "no OS lock primitive -> proceed unguarded"

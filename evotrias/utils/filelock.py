"""Cross-process advisory file lock.

Non-blocking by design: an unavailable lock yields ``False`` rather than
blocking the caller, so contended work can be skipped and retried later
instead of stalling. Deliberately free of any EvoTrias domain knowledge —
``evotrias.logger`` imports this, so this module must not import it back.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager

_LockOps = tuple[Callable[[int], None], Callable[[int], None]]


def _lock_ops() -> _LockOps | None:
    """Return (lock, unlock) for this platform, or None when no OS lock exists."""
    if sys.platform == "win32":
        import msvcrt

        return (
            lambda fd: msvcrt.locking(fd, msvcrt.LK_NBLCK, 1),
            lambda fd: msvcrt.locking(fd, msvcrt.LK_UNLCK, 1),
        )
    try:
        import fcntl

        return (
            lambda fd: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB),
            lambda fd: fcntl.flock(fd, fcntl.LOCK_UN),
        )
    except ImportError:
        return None


@contextmanager
def filelock(lock_path: str | os.PathLike[str]) -> Generator[bool, None, None]:
    """Non-blocking exclusive file lock; yields True when this process holds it.

    Must not log: ``evotrias.logger`` emits while holding its own lock via
    this module, so logging here would recurse into ``emit`` and stall on
    the non-reentrant lock.

        with filelock(path) as held:
            if not held:
                return  # someone else is doing the work
            ...
    """
    lock_path = os.fspath(lock_path)
    parent = os.path.dirname(lock_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    ops = _lock_ops()
    if ops is None:
        yield True  # best-effort
        return
    lock, unlock = ops
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            lock(fd)
        except OSError:  # covers BlockingIOError
            yield False
            return
        try:
            yield True
        finally:
            try:
                unlock(fd)
            except OSError:
                pass
    finally:
        os.close(fd)


@contextmanager
def blocking_filelock(
    lock_path: str | os.PathLike[str],
    timeout: float = 30.0,
    poll_interval: float = 0.02,
) -> Generator[bool, None, None]:
    """Blocking exclusive file lock; spins until held or ``timeout`` elapses.
    Yields True when the lock is held, False on timeout — the caller decides
    what a timed-out acquisition means.

    Must not log: ``evotrias.logger`` emits while holding its own lock via
    this module, so logging here would recurse into ``emit`` and stall on
    the non-reentrant lock.
    """
    lock_path = os.fspath(lock_path)
    parent = os.path.dirname(lock_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    ops = _lock_ops()
    if ops is None:
        yield True  # best-effort
        return
    lock, unlock = ops
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                lock(fd)
                break
            except OSError:  # covers BlockingIOError
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(poll_interval)
        try:
            yield True
        finally:
            try:
                unlock(fd)
            except OSError:
                pass
    finally:
        os.close(fd)

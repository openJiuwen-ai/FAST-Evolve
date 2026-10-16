"""Tests for evotrias.hooks.broker."""

from __future__ import annotations

import threading
import time

import pytest

from evotrias.db import paths
from evotrias.hooks import broker, hook_manager
from evotrias.utils.filelock import blocking_filelock, filelock


@pytest.fixture(autouse=True)
def cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def setup_function(_):
    broker._handlers.clear()


def test_no_handlers_returns_none():
    assert hook_manager.dispatch("claude", "pre_tool_use", {}) is None


def test_priority_descending_order():
    calls = []

    @hook_manager.register("claude", "pre_tool_use", priority=1)
    def low(payload, result, extra):
        calls.append("low")
        return result

    @hook_manager.register("claude", "pre_tool_use", priority=10)
    def high(payload, result, extra):
        calls.append("high")
        return result

    hook_manager.dispatch("claude", "pre_tool_use", {})
    assert calls == ["high", "low"]


def test_result_chains_functionally():
    @hook_manager.register("claude", "post_tool_use", priority=2)
    def first(payload, result, extra):
        return {"step": 1}

    @hook_manager.register("claude", "post_tool_use", priority=1)
    def second(payload, result, extra):
        assert result == {"step": 1}
        return {"step": 2}

    assert hook_manager.dispatch("claude", "post_tool_use", {}) == {"step": 2}


def test_harnesses_lists_registered_harness_names():
    assert hook_manager.harnesses() == set()

    @hook_manager.register("claude", "pre_tool_use")
    def a(payload, result, extra):
        return result

    @hook_manager.register("claude", "post_tool_use")
    def b(payload, result, extra):
        return result

    @hook_manager.register("opencode", "session.idle")
    def c(payload, result, extra):
        return result

    assert hook_manager.harnesses() == {"claude", "opencode"}


def test_return_none_signals_bypass():
    @hook_manager.register("claude", "pre_tool_use", priority=1)
    def handler(payload, result, extra):
        return None  # no opinion

    assert hook_manager.dispatch("claude", "pre_tool_use", {}) is None


def test_crashing_handler_does_not_poison_chain():
    @hook_manager.register("claude", "pre_tool_use", priority=10)
    def boom(payload, result, extra):
        raise RuntimeError("kaboom")

    @hook_manager.register("claude", "pre_tool_use", priority=1)
    def ok(payload, result, extra):
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse"}}

    result = hook_manager.dispatch("claude", "pre_tool_use", {})
    assert result is not None
    assert result["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


def test_extra_data_passed():
    @hook_manager.register("claude", "pre_tool_use", priority=1, extra_data="note")
    def handler(payload, result, extra):
        assert extra == "note"
        return {"ok": True}

    assert hook_manager.dispatch("claude", "pre_tool_use", {}) == {"ok": True}


def test_dispatch_serializes_under_global_lock():
    # msvcrt/flock locks conflict across descriptors even in one process, so
    # two threads dispatching concurrently must not overlap their handlers.
    intervals = []

    @hook_manager.register("claude", "pre_tool_use", priority=1)
    def slow(payload, result, extra):
        start = time.monotonic()
        time.sleep(0.1)
        intervals.append((start, time.monotonic()))
        return result

    threads = [threading.Thread(target=hook_manager.dispatch, args=("claude", "pre_tool_use", {})) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(intervals) == 2
    (a_start, a_end), (b_start, b_end) = sorted(intervals)
    assert b_start >= a_end, "handler runs must not overlap under the dispatch lock"


def test_dispatch_timeout_returns_none_without_running(monkeypatch):
    lock_path = paths.HOOK_QUEUE_LOCK
    monkeypatch.setattr(broker, "blocking_filelock", lambda path: blocking_filelock(path, timeout=0.2))
    calls = []

    @hook_manager.register("claude", "pre_tool_use", priority=1)
    def handler(payload, result, extra):
        calls.append(1)
        return {"ok": True}

    with filelock(lock_path) as held:
        assert held
        assert hook_manager.dispatch("claude", "pre_tool_use", {}) is None
    assert calls == [], "no handler may run when the dispatch lock times out"

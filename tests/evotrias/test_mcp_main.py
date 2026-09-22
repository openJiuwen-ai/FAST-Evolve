"""Tests for the ``python -m evotrias.mcp`` entry point's lifecycle logging."""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def entry(monkeypatch, log):
    module = importlib.import_module("evotrias.mcp.__main__")
    monkeypatch.setattr(module, "logger", log)
    return module


def test_main_logs_start_and_stop(entry, log, monkeypatch):
    monkeypatch.setattr(entry.mcp, "run", lambda: None)
    entry.main()
    infos = log.messages("info")
    assert len(infos) == 2
    assert "starting" in infos[0] and "pid=" in infos[0] and "cwd=" in infos[0]
    assert "stopped" in infos[1]
    assert log.messages("critical") == []


def test_main_logs_crash_and_reraises(entry, log, monkeypatch):
    def _boom():
        raise RuntimeError("transport exploded")

    monkeypatch.setattr(entry.mcp, "run", _boom)
    with pytest.raises(RuntimeError, match="transport exploded"):
        entry.main()
    criticals = log.messages("critical")
    assert len(criticals) == 1
    assert "crashed" in criticals[0]
    assert not any("stopped" in msg for msg in log.messages("info"))


def test_main_logs_interrupt_and_reraises(entry, log, monkeypatch):
    def _interrupt():
        raise KeyboardInterrupt

    monkeypatch.setattr(entry.mcp, "run", _interrupt)
    with pytest.raises(KeyboardInterrupt):
        entry.main()
    assert any("interrupted" in msg for msg in log.messages("info"))
    assert log.messages("critical") == []
    assert not any("stopped" in msg for msg in log.messages("info"))

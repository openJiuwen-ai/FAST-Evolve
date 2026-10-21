# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Tests for the ``python -m evotrias.hooks`` entry point."""

from __future__ import annotations

import importlib
import io
import json
import os
import subprocess
import sys

import pytest

from evotrias.hooks import broker, hook_manager

entry = importlib.import_module("evotrias.hooks.__main__")


class _Log:
    """Records logger calls together with their kwargs (e.g. ``exc_info``)."""

    def __init__(self):
        self.records: list[tuple[str, str, dict]] = []

    def __getattr__(self, level):
        return lambda msg, **kwargs: self.records.append((level, msg, kwargs))

    def criticals(self) -> list[tuple[str, dict]]:
        return [(msg, kwargs) for level, msg, kwargs in self.records if level == "critical"]


@pytest.fixture(autouse=True)
def cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def log(monkeypatch):
    rec = _Log()
    monkeypatch.setattr(entry, "logger", rec)
    return rec


@pytest.fixture
def registry():
    """An isolated handler registry holding one echo handler for claude/pre_tool_use."""
    saved = {key: list(entries) for key, entries in broker._handlers.items()}
    broker._handlers.clear()
    calls = []

    @hook_manager.register("claude", "pre_tool_use")
    def echo(payload, result, extra):
        calls.append(payload)
        return payload.get("reply")

    yield calls
    broker._handlers.clear()
    broker._handlers.update(saved)


def _stdin(monkeypatch, data: bytes) -> None:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data), encoding="utf-8"))


def test_dispatches_payload_and_prints_result(registry, log, monkeypatch, capsys):
    _stdin(monkeypatch, json.dumps({"reply": {"ok": True}}).encode("utf-8"))

    assert entry.main(["claude", "pre_tool_use"]) == 0

    assert registry == [{"reply": {"ok": True}}]
    assert json.loads(capsys.readouterr().out) == {"ok": True}
    assert log.criticals() == []


def test_no_result_prints_nothing(registry, log, monkeypatch, capsys):
    _stdin(monkeypatch, b'{"other": 1}')

    assert entry.main(["claude", "pre_tool_use"]) == 0

    assert capsys.readouterr().out == ""


def test_empty_stdin_dispatches_empty_payload(registry, log, monkeypatch, capsys):
    _stdin(monkeypatch, b"  \n")

    assert entry.main(["claude", "pre_tool_use"]) == 0

    assert registry == [{}]
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("argv", [[], ["claude"], ["claude", "pre_tool_use", "extra"]])
def test_bad_argument_count_fails_without_dispatch(argv, registry, log, monkeypatch, capsys):
    _stdin(monkeypatch, b"{}")

    assert entry.main(argv) == 1

    assert registry == []
    assert "usage:" in capsys.readouterr().err
    assert len(log.criticals()) == 1


def test_unknown_harness_fails_without_dispatch(registry, log, monkeypatch, capsys):
    _stdin(monkeypatch, b"{}")

    assert entry.main(["codex", "pre_tool_use"]) == 1

    assert registry == []
    err = capsys.readouterr().err
    assert "codex" in err and "claude" in err
    assert len(log.criticals()) == 1


def test_unknown_event_is_silently_ignored(registry, log, monkeypatch, capsys):
    _stdin(monkeypatch, b'{"reply": {"ok": true}}')

    assert entry.main(["claude", "no_such_event"]) == 0

    assert registry == []
    assert capsys.readouterr().out == ""
    assert log.criticals() == []


def test_invalid_json_logs_critical_with_traceback_and_raises(registry, log, monkeypatch):
    _stdin(monkeypatch, b"{not json")

    with pytest.raises(json.JSONDecodeError):
        entry.main(["claude", "pre_tool_use"])

    assert registry == []
    (msg, kwargs), *rest = log.criticals()
    assert rest == []
    assert "claude/pre_tool_use" in msg
    assert kwargs.get("exc_info") is True


def test_error_paths_never_exit_with_blocking_code_2(registry, log, monkeypatch):
    # Claude Code blocks the tool call on exit code 2; a broken hook entry must not do that.
    codes = []
    for argv in ([], ["claude"], ["codex", "pre_tool_use"]):
        _stdin(monkeypatch, b"{}")
        codes.append(entry.main(argv))
    assert 2 not in codes


def test_module_entry_reads_utf8_payload_end_to_end(tmp_path):
    # A real subprocess: only there does stdin use the platform default encoding
    # (GBK on Chinese Windows) that a text-mode read would decode wrongly.
    prompt = "求解圆填充问题，并保持中文完整"
    payload = {
        "session_id": "sess-none",
        "hook_event_name": "PreToolUse",
        "tool_name": "Agent",
        "tool_input": {
            "isolation": "worktree",
            "prompt": prompt,
            "subagent_type": "evotrias-init-explorer",
        },
    }
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHONIO")}
    env.pop("PYTHONUTF8", None)

    proc = subprocess.run(
        [sys.executable, "-m", "evotrias.hooks", "claude", "pre_tool_use"],
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
        cwd=tmp_path,
        env=env,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    updated = json.loads(proc.stdout)["hookSpecificOutput"]["updatedInput"]
    assert "isolation" not in updated
    assert updated["prompt"] == prompt

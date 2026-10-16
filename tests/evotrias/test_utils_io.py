"""Tests for evotrias.utils.io."""

from __future__ import annotations

import json
import os

import pytest

from evotrias.utils import io


def test_write_json_atomic_round_trips_and_ends_with_newline(tmp_path, log):
    path = tmp_path / "nested" / "state.json"
    io.write_json_atomic(path, {"b": 1, "a": {"x": [1, 2]}})
    assert io.read_json(path, {}) == {"b": 1, "a": {"x": [1, 2]}}
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert log.warnings == []


def test_write_json_atomic_leaves_no_tmp_files(tmp_path, log):
    path = tmp_path / "state.json"
    io.write_json_atomic(path, {"k": "v"})
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_write_json_atomic_logs_debug_with_path_and_size(tmp_path, log):
    path = tmp_path / "state.json"
    io.write_json_atomic(path, {"k": "v"})
    debugs = log.messages("debug")
    assert len(debugs) == 1
    assert "state.json" in debugs[0]
    assert f"{path.stat().st_size} bytes" in debugs[0]


def test_write_json_atomic_failure_leaves_previous_content(tmp_path, log, monkeypatch):
    path = tmp_path / "state.json"
    io.write_json_atomic(path, {"v": 1})

    def _boom(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError, match="simulated"):
        io.write_json_atomic(path, {"v": 2})
    assert io.read_json(path, {}) == {"v": 1}


def test_write_json_atomic_on_disk_format(tmp_path, log):
    path = tmp_path / "state.json"
    data = {"z": "中文", "a": [1, {"y": 2, "x": 1}]}
    io.write_json_atomic(path, data)
    expected = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    assert path.read_text(encoding="utf-8") == expected


def test_write_json_atomic_can_keep_key_order(tmp_path, log):
    path = tmp_path / "settings.json"
    io.write_json_atomic(path, {"z": 1, "a": {"y": 2, "x": 1}}, sort_keys=False)
    assert path.read_text(encoding="utf-8").index('"z"') < path.read_text(encoding="utf-8").index('"a"')
    assert list(json.loads(path.read_text(encoding="utf-8"))["a"]) == ["y", "x"]


def test_read_json_missing_file_returns_default_silently(tmp_path, log):
    default = {"fresh": True}
    assert io.read_json(tmp_path / "absent.json", default) is default
    assert log.warnings == []


def test_read_json_corrupt_file_returns_default_with_warning(tmp_path, log):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    assert io.read_json(path, {"d": 1}) == {"d": 1}
    assert len(log.warnings) == 1
    assert "state.json" in log.warnings[0]


def test_read_json_non_dict_top_level_returns_default_with_warning(tmp_path, log):
    path = tmp_path / "state.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert io.read_json(path, {"d": 1}) == {"d": 1}
    assert len(log.warnings) == 1
    assert "list" in log.warnings[0]


def test_append_jsonl_preserves_order(tmp_path, log):
    path = tmp_path / "nested" / "events.jsonl"
    for i in range(3):
        io.append_jsonl(path, {"i": i})
    assert io.read_jsonl(path) == [{"i": 0}, {"i": 1}, {"i": 2}]
    assert log.warnings == []


def test_append_jsonl_logs_debug_per_row(tmp_path, log):
    path = tmp_path / "events.jsonl"
    for i in range(3):
        io.append_jsonl(path, {"i": i})
    debugs = log.messages("debug")
    assert len(debugs) == 3
    assert all("events.jsonl" in msg and "bytes" in msg for msg in debugs)


def test_append_jsonl_on_disk_format(tmp_path, log):
    path = tmp_path / "events.jsonl"
    io.append_jsonl(path, {"b": 1, "a": "中"})
    assert path.read_text(encoding="utf-8") == '{"a": "中", "b": 1}\n'


def test_read_jsonl_skips_malformed_lines_with_warning(tmp_path, log):
    path = tmp_path / "events.jsonl"
    path.write_text('{"i": 0}\n{broken\n\n{"i": 2}\n', encoding="utf-8")
    assert io.read_jsonl(path) == [{"i": 0}, {"i": 2}]
    assert len(log.warnings) == 1
    assert "line 2" in log.warnings[0]


def test_read_jsonl_missing_file_returns_empty(tmp_path, log):
    assert io.read_jsonl(tmp_path / "absent.jsonl") == []
    assert log.warnings == []

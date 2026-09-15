"""Tests for evotrias.db.base."""

from __future__ import annotations

import dataclasses
import importlib
import json
import pkgutil
from dataclasses import dataclass, field

import pytest

from evotrias.db import base


class _Recorder:
    def __init__(self):
        self.warnings: list[str] = []

    def warning(self, msg, **kwargs):
        self.warnings.append(msg)

    def debug(self, msg, **kwargs):
        pass

    info = error = critical = debug


@pytest.fixture
def log(monkeypatch):
    # evotrias.logger writes to ./.evotrias/logs relative to cwd and does not
    # propagate, so swap it for a recorder instead of relying on caplog.
    rec = _Recorder()
    monkeypatch.setattr(base, "logger", rec)
    return rec


# --- primitives -------------------------------------------------------------


def test_write_json_atomic_round_trips_and_ends_with_newline(tmp_path, log):
    path = tmp_path / "nested" / "state.json"
    base.write_json_atomic(path, {"b": 1, "a": {"x": [1, 2]}})
    assert base.read_json(path, {}) == {"b": 1, "a": {"x": [1, 2]}}
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert log.warnings == []


def test_write_json_atomic_leaves_no_tmp_files(tmp_path, log):
    path = tmp_path / "state.json"
    base.write_json_atomic(path, {"k": "v"})
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_read_json_missing_file_returns_default_silently(tmp_path, log):
    default = {"fresh": True}
    assert base.read_json(tmp_path / "absent.json", default) is default
    assert log.warnings == []


def test_read_json_corrupt_file_returns_default_with_warning(tmp_path, log):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    assert base.read_json(path, {"d": 1}) == {"d": 1}
    assert len(log.warnings) == 1
    assert "state.json" in log.warnings[0]


def test_read_json_non_dict_top_level_returns_default_with_warning(tmp_path, log):
    path = tmp_path / "state.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert base.read_json(path, {"d": 1}) == {"d": 1}
    assert len(log.warnings) == 1
    assert "list" in log.warnings[0]


def test_append_jsonl_preserves_order(tmp_path, log):
    path = tmp_path / "nested" / "events.jsonl"
    for i in range(3):
        base.append_jsonl(path, {"i": i})
    assert base.read_jsonl(path) == [{"i": 0}, {"i": 1}, {"i": 2}]
    assert log.warnings == []


def test_read_jsonl_skips_malformed_lines_with_warning(tmp_path, log):
    path = tmp_path / "events.jsonl"
    path.write_text('{"i": 0}\n{broken\n\n{"i": 2}\n', encoding="utf-8")
    assert base.read_jsonl(path) == [{"i": 0}, {"i": 2}]
    assert len(log.warnings) == 1
    assert "line 2" in log.warnings[0]


def test_read_jsonl_missing_file_returns_empty(tmp_path, log):
    assert base.read_jsonl(tmp_path / "absent.jsonl") == []
    assert log.warnings == []


# --- DBList -----------------------------------------------------------------


def test_dblist_missing_file_is_empty(tmp_path, log):
    log_store = base.DBList(tmp_path / "events.jsonl")
    assert list(log_store) == []
    assert len(log_store) == 0


def test_dblist_append_then_iterate(tmp_path, log):
    log_store = base.DBList(tmp_path / "events.jsonl")
    log_store.append({"n": 1})
    log_store.append({"n": 2})
    assert list(log_store) == [{"n": 1}, {"n": 2}]
    assert len(log_store) == 2


@dataclass
class _Event:
    kind: str
    value: int


class _TypedList(base.DBList):
    def _encode(self, item):
        return {"kind": item.kind, "value": item.value}

    def _decode(self, row):
        return _Event(kind=row["kind"], value=row["value"])


def test_dblist_subclass_codec_round_trips_dataclass(tmp_path, log):
    log_store = _TypedList(tmp_path / "events.jsonl")
    log_store.append(_Event("start", 1))
    [got] = list(log_store)
    assert isinstance(got, _Event)
    assert got == _Event("start", 1)


# --- DBObject ---------------------------------------------------------------


class _State(base.DBObject):
    name: str = ""
    count: int = 0
    tags: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)


def test_dbobject_subclass_becomes_dataclass_without_generated_init():
    assert dataclasses.is_dataclass(_State)
    assert [f.name for f in dataclasses.fields(_State)] == ["name", "count", "tags", "meta"]
    # Concrete stores define __init__(self, run_id); what must never happen is
    # a dataclass-generated __init__ taking field kwargs.
    assert _State.__dataclass_params__.init is False


def test_dbobject_field_without_default_fails_at_definition():
    with pytest.raises(TypeError, match="needs a default"):

        class _Bad(base.DBObject):
            name: str


def test_dbobject_declaring_created_at_fails_at_definition():
    with pytest.raises(TypeError, match="created_at is managed"):

        class _Bad(base.DBObject):
            created_at: str = ""


def test_dbobject_field_with_bare_field_call_fails_at_definition():
    with pytest.raises(TypeError, match="needs a default"):

        class _Bad(base.DBObject):
            tags: list = field()


def test_every_db_object_subclass_in_package_is_well_formed():
    import evotrias.db as pkg

    for info in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
        importlib.import_module(info.name)

    def walk(cls):
        for sub in cls.__subclasses__():
            yield sub
            yield from walk(sub)

    for cls in walk(base.DBObject):
        if not cls.__module__.startswith("evotrias.db."):
            continue
        assert dataclasses.is_dataclass(cls), cls
        assert cls.__dataclass_params__.init is False, cls
        for f in dataclasses.fields(cls):
            has_default = f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING
            assert has_default, (cls, f.name)


def test_dbobject_missing_file_loads_defaults(tmp_path, log):
    state = _State(tmp_path / "state.json")
    assert (state.name, state.count, state.tags, state.meta) == ("", 0, [], {})
    assert log.warnings == []


def test_dbobject_loads_fields_and_ignores_unknown_keys(tmp_path, log):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"name": "x", "count": 3, "extra": True}), encoding="utf-8")
    state = _State(path)
    assert (state.name, state.count) == ("x", 3)
    assert not hasattr(state, "extra")


def test_dbobject_with_block_writes_on_exit(tmp_path, log):
    path = tmp_path / "state.json"
    with _State(path) as state:
        state.name = "y"
        state.count = 7
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk.pop("created_at")
    assert on_disk == {"name": "y", "count": 7, "tags": [], "meta": {}}


def test_dbobject_without_with_never_writes(tmp_path, log):
    path = tmp_path / "state.json"
    state = _State(path)
    state.name = "unsaved"
    assert not path.exists()


def test_dbobject_exception_inside_with_does_not_write(tmp_path, log):
    path = tmp_path / "state.json"
    base.write_json_atomic(path, {"name": "before"})
    with pytest.raises(RuntimeError):
        with _State(path) as state:
            state.name = "after"
            raise RuntimeError("boom")
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "before"


def test_dbobject_nested_mutation_is_persisted(tmp_path, log):
    path = tmp_path / "state.json"
    with _State(path) as state:
        state.tags.append("a")
        state.meta["k"] = {"deep": 1}
    reloaded = _State(path)
    assert reloaded.tags == ["a"]
    assert reloaded.meta == {"k": {"deep": 1}}


@dataclass
class _Inner:
    x: int = 0
    y: str = ""


class _Outer(base.DBObject):
    inner: _Inner = field(default_factory=_Inner)
    label: str = ""


def test_dbobject_nested_dataclass_round_trips(tmp_path, log):
    path = tmp_path / "state.json"
    with _Outer(path) as outer:
        outer.inner.x = 5
        outer.inner.y = "five"
        outer.label = "L"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    on_disk.pop("created_at")
    assert on_disk == {"inner": {"x": 5, "y": "five"}, "label": "L"}
    reloaded = _Outer(path)
    assert isinstance(reloaded.inner, _Inner)
    assert reloaded.inner == _Inner(x=5, y="five")


def test_dbobject_nested_dataclass_ignores_unknown_keys(tmp_path, log):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"inner": {"x": 1, "stale": True}}), encoding="utf-8")
    reloaded = _Outer(path)
    assert reloaded.inner == _Inner(x=1, y="")


def test_dbobject_created_at_is_stamped_once_on_first_write(tmp_path, log):
    path = tmp_path / "state.json"
    assert _State(path).created_at == ""
    with _State(path) as state:
        state.name = "first"
    first = _State(path).created_at
    assert first
    with _State(path) as state:
        state.name = "second"
    assert _State(path).created_at == first
    assert json.loads(path.read_text(encoding="utf-8"))["created_at"] == first


def test_dbobject_created_at_not_stamped_without_write(tmp_path, log):
    path = tmp_path / "state.json"
    state = _State(path)
    state.name = "x"
    assert state.created_at == ""
    assert not path.exists()

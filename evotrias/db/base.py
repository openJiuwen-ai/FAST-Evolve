"""JSON/JSONL primitives and the two store base classes.

Two shapes cover every persisted file:

* ``DBList`` -- an append-only JSONL event log with a dataclass schema.
  ``append`` takes a schema instance and writes one line; iterating re-reads
  the file and yields schema instances.
* ``DBObject`` -- a JSON snapshot backed by a dataclass.  Subclasses declare
  fields (all with defaults); constructing loads, leaving a ``with`` block
  writes the whole object back atomically.

Callers own the transaction boundary::

    state = RunState(path)            # read-only view
    with RunState(path) as state:     # read-modify-write
        state.stop_reason = "plateau"

Reads are tolerant: a missing file yields defaults silently (first run), a
corrupt one yields defaults with a warning.  Locking is the caller's concern.
"""

from __future__ import annotations

import dataclasses
import enum
import inspect
import json
import os
import threading
import typing
from pathlib import Path
from typing import Any, Dict, Iterator, List

from evotrias.logger import logger
from evotrias.utils.time import utc_now_iso


def read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning(f"read_json: unreadable {path}, using default. {exc}")
        return default
    if not isinstance(data, dict):
        logger.warning(f"read_json: {path} top level is {type(data).__name__}, not dict; using default")
        return default
    return data


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    logger.warning(f"read_jsonl: skipping malformed line {lineno} in {path}")
                    continue
                if not isinstance(row, dict):
                    logger.warning(f"read_jsonl: skipping non-object line {lineno} in {path}")
                    continue
                rows.append(row)
    except OSError as exc:
        logger.warning(f"read_jsonl: unreadable {path}, returning what was parsed. {exc}")
    return rows


def write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _dataclass_from_dict(cls: type, data: Dict[str, Any]) -> Any:
    """Build a dataclass from a dict, ignoring unknown keys and rebuilding nested dataclass fields."""
    hints = typing.get_type_hints(cls)
    known = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: _rebuild(hints.get(k), v) for k, v in data.items() if k in known})


def _rebuild(hint: Any, value: Any) -> Any:
    """Turn loaded JSON back into the shape ``hint`` declares.

    Handles a dataclass or an ``Enum`` and the containers ``Optional[X]``,
    ``List[X]`` and ``Dict[str, X]`` around one; anything else is returned
    as-is.
    """
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    if isinstance(hint, type) and issubclass(hint, enum.Enum) and not isinstance(value, enum.Enum):
        return hint(value)
    if origin is typing.Union:
        inner = [a for a in args if a is not type(None)]
        return _rebuild(inner[0], value) if len(inner) == 1 and value is not None else value
    if isinstance(value, dict):
        if _is_dataclass_type(hint):
            return _dataclass_from_dict(hint, value)
        if origin is dict and len(args) == 2:
            return {k: _rebuild(args[1], v) for k, v in value.items()}
    if isinstance(value, list) and origin is list and len(args) == 1:
        return [_rebuild(args[0], v) for v in value]
    return value


def _is_dataclass_type(hint: Any) -> bool:
    return isinstance(hint, type) and dataclasses.is_dataclass(hint)


def _to_json(value: Any) -> Any:
    """Recursively replace Enum members with their values so ``json.dumps`` accepts the result."""
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _to_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_json(v) for v in value]
    return value


class DBList:
    """Append-only JSONL log whose rows are instances of ``schema``.

    One plain dataclass constrains both sides: ``append`` only accepts it,
    iteration rebuilds it from each line (unknown keys ignored, missing keys
    take the dataclass defaults, nested dataclass fields rebuilt one level).
    """

    def __init__(self, path: Path, schema: type):
        self._path = Path(path)
        self._schema = schema

    def __iter__(self) -> Iterator[Any]:
        for row in read_jsonl(self._path):
            yield _dataclass_from_dict(self._schema, row)

    def __len__(self) -> int:
        return len(read_jsonl(self._path))

    def append(self, item: Any) -> None:
        if not isinstance(item, self._schema):
            raise TypeError(f"{type(self).__name__}.append expects {self._schema.__name__}, got {type(item).__name__}")
        append_jsonl(self._path, _to_json(dataclasses.asdict(item)))


class DBObject:
    """JSON snapshot backed by a dataclass.

    Subclasses only declare fields; ``__init_subclass__`` turns them into a
    ``dataclass(init=False)`` and rejects any field without a default, so a
    malformed store fails at import rather than on first load.  Unknown keys in
    the file are ignored.  Fields typed as a dataclass or ``Enum``, or as
    ``Optional``, ``List`` or ``Dict[str, ...]`` of one, are rebuilt from JSON
    on load and enums are written as their values; other nested shapes need
    the subclass to override ``_load``/``_dump``.

    ``created_at`` and ``updated_at`` are managed here, not declared by
    subclasses: both empty until the file is first written; ``created_at`` is
    stamped once, ``updated_at`` on every write.  They are deliberately plain
    attributes rather than dataclass fields so ``==``, ``repr`` and ``asdict``
    see only the payload -- which is why ``__exit__`` adds them to the dumped
    dict by hand.
    """

    _path: Path
    created_at: str
    updated_at: str

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        for name in inspect.get_annotations(cls):
            if name in ("created_at", "updated_at"):
                raise TypeError(f"{cls.__name__}.{name} is managed by DBObject; do not declare it")
            value = cls.__dict__.get(name, dataclasses.MISSING)
            if isinstance(value, dataclasses.Field):
                if value.default is dataclasses.MISSING and value.default_factory is dataclasses.MISSING:
                    value = dataclasses.MISSING
            if value is dataclasses.MISSING:
                raise TypeError(f"{cls.__name__}.{name} needs a default (file may not exist yet)")
        dataclasses.dataclass(init=False)(cls)

    def __init__(self, path: Path):
        self._path = Path(path)
        data = read_json(self._path, {})
        self.created_at = str(data.get("created_at") or "")
        self.updated_at = str(data.get("updated_at") or "")
        self._load(data)

    def _load(self, data: Dict[str, Any]) -> None:
        hints = typing.get_type_hints(type(self))
        for field in dataclasses.fields(self):
            if field.name in data:
                value = _rebuild(hints.get(field.name), data[field.name])
            elif field.default is not dataclasses.MISSING:
                value = field.default
            else:
                value = field.default_factory()
            setattr(self, field.name, value)

    def _dump(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.updated_at = utc_now_iso()
            if not self.created_at:
                self.created_at = self.updated_at
            payload = _to_json(self._dump())
            payload["created_at"] = self.created_at
            payload["updated_at"] = self.updated_at
            write_json_atomic(self._path, payload)

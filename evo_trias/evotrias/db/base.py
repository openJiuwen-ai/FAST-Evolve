"""The two store base classes behind every file under ``.evotrias/db``.

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

File format and read tolerance are ``evotrias.utils.io``'s; locking is the
caller's concern.
"""

from __future__ import annotations

import dataclasses
import enum
import types
import typing
from pathlib import Path
from typing import Any, ClassVar, Dict, Iterator

from evotrias.utils.io import append_jsonl, read_json, read_jsonl, write_json_atomic
from evotrias.utils.time import utc_now_iso


def _fields_from_json(cls: type, data: Dict[str, Any]) -> Dict[str, Any]:
    """Value for every field of dataclass ``cls``: rebuilt from ``data``, else its default.

    Unknown keys in ``data`` are ignored; a field with no default that is
    absent from ``data`` is left out so ``cls(**...)`` raises as usual.
    """
    hints = typing.get_type_hints(cls)
    values: Dict[str, Any] = {}
    for field in dataclasses.fields(cls):
        if field.name in data:
            values[field.name] = _rebuild(hints[field.name], data[field.name])
        elif field.default is not dataclasses.MISSING:
            values[field.name] = field.default
        elif field.default_factory is not dataclasses.MISSING:
            values[field.name] = field.default_factory()
    return values


def _rebuild(hint: Any, value: Any) -> Any:
    """Turn loaded JSON back into the shape ``hint`` declares.

    Handles a dataclass or an ``Enum``, and ``Optional[X]`` / ``X | None``,
    ``List[X]`` and ``Dict[str, X]`` around one.  Anything else -- including
    a value whose JSON type does not match the hint -- is returned as-is.
    """
    if value is None:
        return None
    origin, args = typing.get_origin(hint), typing.get_args(hint)
    if origin in (typing.Union, types.UnionType):
        inner = [a for a in args if a is not type(None)]
        return _rebuild(inner[0], value) if len(inner) == 1 else value
    if origin is list and isinstance(value, list):
        return [_rebuild(args[0], v) for v in value]
    if origin is dict and isinstance(value, dict):
        return {k: _rebuild(args[1], v) for k, v in value.items()}
    if isinstance(hint, type):
        if issubclass(hint, enum.Enum):
            return hint(value)
        if dataclasses.is_dataclass(hint) and isinstance(value, dict):
            return hint(**_fields_from_json(hint, value))
    return value


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
    take the dataclass defaults, nested shapes rebuilt per ``_rebuild``).
    """

    def __init__(self, path: Path, schema: type):
        self._path = Path(path)
        self._schema = schema

    def __iter__(self) -> Iterator[Any]:
        for row in read_jsonl(self._path):
            yield self._schema(**_fields_from_json(self._schema, row))

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
    the file are ignored; field values are rebuilt per ``_rebuild`` on load
    and enums are written as their values.

    ``created_at`` and ``updated_at`` are managed here, not declared by
    subclasses: both empty until the file is first written; ``created_at`` is
    stamped once, ``updated_at`` on every write.  They are deliberately plain
    attributes rather than dataclass fields so ``==``, ``repr`` and ``asdict``
    see only the payload -- which is why ``__exit__`` adds them to the dumped
    dict by hand.
    """

    # Set on every subclass by the dataclass decorator in __init_subclass__;
    # declared here so type checkers accept the base as a DataclassInstance.
    __dataclass_fields__: ClassVar[Dict[str, dataclasses.Field[Any]]]

    _path: Path
    created_at: str
    updated_at: str

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        dataclasses.dataclass(init=False)(cls)
        for field in dataclasses.fields(cls):
            if field.name in ("created_at", "updated_at"):
                raise TypeError(f"{cls.__name__}.{field.name} is managed by DBObject; do not declare it")
            if field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING:
                raise TypeError(f"{cls.__name__}.{field.name} needs a default (file may not exist yet)")

    def __init__(self, path: Path):
        self._path = Path(path)
        data = read_json(self._path, {})
        self.created_at = str(data.get("created_at") or "")
        self.updated_at = str(data.get("updated_at") or "")
        for name, value in _fields_from_json(type(self), data).items():
            setattr(self, name, value)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.updated_at = utc_now_iso()
            if not self.created_at:
                self.created_at = self.updated_at
            payload = _to_json(dataclasses.asdict(self))
            payload["created_at"] = self.created_at
            payload["updated_at"] = self.updated_at
            write_json_atomic(self._path, payload)

"""JSON/JSONL primitives and the two store base classes.

Two shapes cover every persisted file:

* ``DBList`` -- an append-only JSONL event log.  Iterating re-reads the file;
  ``append`` writes one line.
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
    """Build a plain dataclass from a dict, ignoring unknown keys."""
    known = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


class DBList:
    """Append-only JSONL log. Subclasses may override ``_encode``/``_decode``."""

    def __init__(self, path: Path):
        self._path = Path(path)

    def __iter__(self) -> Iterator[Any]:
        for row in read_jsonl(self._path):
            yield self._decode(row)

    def __len__(self) -> int:
        return len(read_jsonl(self._path))

    def append(self, item: Any) -> None:
        append_jsonl(self._path, self._encode(item))

    def _encode(self, item: Any) -> Dict[str, Any]:
        return item

    def _decode(self, row: Dict[str, Any]) -> Any:
        return row


class DBObject:
    """JSON snapshot backed by a dataclass.

    Subclasses only declare fields; ``__init_subclass__`` turns them into a
    ``dataclass(init=False)`` and rejects any field without a default, so a
    malformed store fails at import rather than on first load.  Unknown keys in
    the file are ignored.  A field typed as a plain dataclass is rebuilt from
    its dict on load; other nested shapes (e.g. ``dict[str, SomeDataclass]``)
    need the subclass to override ``_load``/``_dump``.

    ``created_at`` is managed here, not declared by subclasses: empty until the
    file is first written, stamped then, never changed afterwards.
    """

    _path: Path
    created_at: str

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        for name in inspect.get_annotations(cls):
            if name == "created_at":
                raise TypeError(f"{cls.__name__}.created_at is managed by DBObject; do not declare it")
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
        self._load(data)

    def _load(self, data: Dict[str, Any]) -> None:
        hints = typing.get_type_hints(type(self))
        for field in dataclasses.fields(self):
            if field.name in data:
                value = data[field.name]
                hint = hints.get(field.name)
                if isinstance(value, dict) and isinstance(hint, type) and dataclasses.is_dataclass(hint):
                    value = _dataclass_from_dict(hint, value)
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
            if not self.created_at:
                self.created_at = utc_now_iso()
            payload = self._dump()
            payload["created_at"] = self.created_at
            write_json_atomic(self._path, payload)

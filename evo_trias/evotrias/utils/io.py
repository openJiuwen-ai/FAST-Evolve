"""JSON / JSONL file primitives shared by every module that touches disk.

Write conventions: ``indent=2, ensure_ascii=False, sort_keys=True`` plus a
trailing newline for JSON; one compact object per line for JSONL.  JSON is
written to ``.{name}.{pid}.{tid}.tmp`` next to the target and moved into
place with ``os.replace`` so readers never see a partial file.

Reads are tolerant: a missing file yields the default silently (a first run
is normal), a corrupt JSON file yields the default with a warning, and a
malformed JSONL line is skipped with a warning.  Every write logs one debug
line with the path and the size written.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from evotrias.logger import logger


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
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


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    logger.debug(f"write_json_atomic: {path} ({path.stat().st_size} bytes)")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
    logger.debug(f"append_jsonl: {path} +{len(line.encode('utf-8'))} bytes")

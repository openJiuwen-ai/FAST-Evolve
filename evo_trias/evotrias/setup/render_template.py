"""Render a template tree into a workspace: ``*.jinja`` files are rendered, everything else is copied."""

from __future__ import annotations

from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined

_SUFFIX = ".jinja"

_env = Environment(
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
    autoescape=False,
)


def render_tree(src: Traversable | Path, dst: Path, context: dict[str, Any]) -> list[Path]:
    """Render/copy every file under ``src`` into ``dst``; returns the files written. Never deletes anything."""
    written: list[Path] = []
    for entry in sorted(src.iterdir(), key=lambda e: e.name):
        if entry.is_dir():
            written += render_tree(entry, dst / entry.name, context)
            continue
        dst.mkdir(parents=True, exist_ok=True)
        if entry.name.endswith(_SUFFIX):
            target = dst / entry.name[: -len(_SUFFIX)]
            source = entry.read_bytes().decode("utf-8").replace("\r\n", "\n")
            text = _env.from_string(source).render(context)
            target.write_bytes(text.encode("utf-8"))
        else:
            target = dst / entry.name
            target.write_bytes(entry.read_bytes())
        written.append(target)
    return written

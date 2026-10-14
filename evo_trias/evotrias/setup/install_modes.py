"""Install strategies: how one template entry (a directory or a file) lands in the workspace.

Every strategy takes ``(src, dst, context)`` and returns the files it wrote; none deletes anything.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined

from evotrias.enums_and_consts import InstallMode
from evotrias.utils.io import read_json, write_json_atomic

_SUFFIX = ".jinja"

_env = Environment(
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
    autoescape=False,
)


def render_jinja(src: Traversable | Path, dst: Path, context: dict[str, Any]) -> list[Path]:
    """Render/copy every file under the directory ``src`` into ``dst``: ``*.jinja`` files are rendered
    and lose the suffix, everything else is copied."""
    written: list[Path] = []
    for entry in sorted(src.iterdir(), key=lambda e: e.name):
        if entry.is_dir():
            written += render_jinja(entry, dst / entry.name, context)
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


def merge_json_data(user: Any, template: Any) -> Any:
    """Objects merge key by key; arrays get the template's elements not already present; anything
    else (scalars, mismatched types) takes the template's value.  Neither input is modified."""
    if isinstance(user, dict) and isinstance(template, dict):
        merged = dict(user)
        for key, value in template.items():
            merged[key] = merge_json_data(user[key], value) if key in user else value
        return merged
    if isinstance(user, list) and isinstance(template, list):
        return user + [item for item in template if item not in user]
    return template


def merge_json(src: Traversable | Path, dst: Path, context: dict[str, Any]) -> list[Path]:
    """Merge the JSON file ``src`` into ``dst``; an unreadable or non-object ``dst`` counts as empty.
    ``dst`` is rewritten only when the merge changes it, keeping the user's key order."""
    user = read_json(dst, {})
    merged = merge_json_data(user, json.loads(src.read_text(encoding="utf-8")))
    if merged == user and dst.exists():
        return []
    write_json_atomic(dst, merged, sort_keys=False)
    return [dst]


def overwrite(src: Traversable | Path, dst: Path, context: dict[str, Any]) -> list[Path]:
    """Copy the file ``src`` to ``dst`` byte for byte."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    return [dst]


STRATEGIES: dict[InstallMode, Callable[[Traversable | Path, Path, dict[str, Any]], list[Path]]] = {
    InstallMode.RENDER_JINJA: render_jinja,
    InstallMode.MERGE_JSON: merge_json,
    InstallMode.OVERWRITE: overwrite,
}

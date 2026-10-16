"""``install(harness, workspace)``: set a workspace up for one harness (design.md §6.3).

Each install entry is applied by the strategy of its ``InstallMode``.  Every strategy takes
``(src, dst, context)``; none deletes anything.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined

from evotrias.enums_and_consts import InstallMode
from evotrias.setup.harness.base import Harness
from evotrias.setup.workspace import ACTIVE_HARNESS, write_active_harness
from evotrias.utils.io import read_json, write_json_atomic

TEMPLATES = files("evotrias") / "templates"

_env = Environment(
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
    autoescape=False,
)


def render_jinja(src: Traversable | Path, dst: Path, context: dict[str, Any]) -> None:
    """Render/copy every file under the directory ``src`` into ``dst``: ``*.jinja`` files are rendered
    and lose the suffix, everything else is copied."""
    suffix = ".jinja"
    for entry in sorted(src.iterdir(), key=lambda e: e.name):
        if entry.is_dir():
            render_jinja(entry, dst / entry.name, context)
            continue
        dst.mkdir(parents=True, exist_ok=True)
        if entry.name.endswith(suffix):
            source = entry.read_bytes().decode("utf-8").replace("\r\n", "\n")
            text = _env.from_string(source).render(context)
            (dst / entry.name.removesuffix(suffix)).write_bytes(text.encode("utf-8"))
        else:
            (dst / entry.name).write_bytes(entry.read_bytes())


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


def merge_json(src: Traversable | Path, dst: Path, context: dict[str, Any]) -> None:
    """Merge the JSON file ``src`` into ``dst``; an unreadable or non-object ``dst`` counts as empty.
    ``dst`` is rewritten only when the merge changes it, keeping the user's key order."""
    user = read_json(dst, {})
    merged = merge_json_data(user, json.loads(src.read_text(encoding="utf-8")))
    if merged != user or not dst.exists():
        write_json_atomic(dst, merged, sort_keys=False)


def overwrite(src: Traversable | Path, dst: Path, context: dict[str, Any]) -> None:
    """Copy the file ``src`` to ``dst`` byte for byte."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())


STRATEGIES: dict[InstallMode, Callable[[Traversable | Path, Path, dict[str, Any]], None]] = {
    InstallMode.RENDER_JINJA: render_jinja,
    InstallMode.MERGE_JSON: merge_json,
    InstallMode.OVERWRITE: overwrite,
}


def install(harness: Harness, workspace: Path) -> Iterator[str]:
    """Apply the install entries of ``harness`` to ``workspace`` one by one, then record it as the
    active harness.  Yields each step's destination once the step is done, so a caller can show
    progress.  Lazy: nothing is installed unless the caller iterates.  Files outside the template
    list are never touched or deleted."""
    context = harness.template_context()
    for src, dst, mode in harness.install_entries:
        STRATEGIES[mode](TEMPLATES / src, workspace / dst, context)
        yield dst
    write_active_harness(workspace, harness.name)
    yield ACTIVE_HARNESS.as_posix()

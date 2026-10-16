"""Tests for the harness template contexts (claude / opencode)."""

from __future__ import annotations

import pytest
import yaml
from jinja2 import UndefinedError

from evotrias.setup.harness.claude import ClaudeHarness
from evotrias.setup.harness.opencode import OpencodeHarness
from evotrias.setup.install import render_jinja

HARNESSES = [ClaudeHarness(), OpencodeHarness()]
TEMPLATE_TOOLS = ("skill", "read", "write", "edit", "glob", "grep", "bash", "webfetch")


def _render(harness, template: str, tmp_path) -> str:
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (src / "t.md.jinja").write_bytes(template.encode("utf-8"))
    render_jinja(src, dst, harness.template_context())
    return (dst / "t.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("harness", "mcp_name", "background"),
    [
        (ClaudeHarness(), "mcp__evotrias__select_parent", "run_in_background"),
        (OpencodeHarness(), "evotrias_select_parent", "background"),
    ],
)
def test_mcp_and_background_arg(harness, mcp_name, background, tmp_path):
    out = _render(harness, '{{ harness }}|{{ mcp("select_parent") }}|{{ background_arg }}\n', tmp_path)

    assert out == f"{harness.name}|{mcp_name}|{background}\n"


@pytest.mark.parametrize(
    ("harness", "expected"),
    [
        (ClaudeHarness(), "Skill Read Write Edit Glob Grep Bash WebFetch"),
        (OpencodeHarness(), "skill read write edit glob grep bash webfetch"),
    ],
)
def test_template_tool_names(harness, expected, tmp_path):
    template = " ".join(f"{{{{ tool.{t} }}}}" for t in TEMPLATE_TOOLS) + "\n"

    assert _render(harness, template, tmp_path) == expected + "\n"


@pytest.mark.parametrize("harness", HARNESSES, ids=lambda h: h.name)
def test_unknown_tool_attribute_fails_rendering(harness, tmp_path):
    with pytest.raises(UndefinedError):
        _render(harness, "{{ tool.reed }}\n", tmp_path)


_AGENT_TEMPLATE = """---
{{ agent_frontmatter(name="evotrias-task-executor", description="Runs one task.") }}
---

body
"""


def _frontmatter(text: str) -> dict:
    _, header, body = text.split("---\n", 2)
    assert body == "\nbody\n"
    return yaml.safe_load(header)


def test_claude_agent_frontmatter_has_only_name_and_description(tmp_path):
    # tools / model fall back to Claude Code defaults.
    meta = _frontmatter(_render(ClaudeHarness(), _AGENT_TEMPLATE, tmp_path))

    assert meta == {"name": "evotrias-task-executor", "description": "Runs one task."}


def test_opencode_agent_frontmatter_has_description_and_subagent_mode(tmp_path):
    # opencode takes the agent name from the file name; tools / model fall back to defaults.
    # mode is explicit: its default "all" would also offer the agent as a primary agent.
    meta = _frontmatter(_render(OpencodeHarness(), _AGENT_TEMPLATE, tmp_path))

    assert meta == {"description": "Runs one task.", "mode": "subagent"}


@pytest.mark.parametrize("harness", HARNESSES, ids=lambda h: h.name)
def test_extra_frontmatter_fields_are_not_written(harness):
    # Each harness picks the fields it writes; anything else the template passes is left out.
    fields = harness.agent_frontmatter_fields(name="a", description="d", color="blue")

    assert "color" not in fields


@pytest.mark.parametrize("harness", HARNESSES, ids=lambda h: h.name)
def test_missing_required_frontmatter_field_fails(harness):
    with pytest.raises(KeyError, match="description"):
        harness.agent_frontmatter_fields(name="a")

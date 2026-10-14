"""Tests for evotrias.setup.install_modes: the install strategies and the JSON merge rules."""

from __future__ import annotations

import json

import pytest
from jinja2 import UndefinedError

from evotrias.enums_and_consts import InstallMode
from evotrias.setup.install_modes import STRATEGIES, merge_json, merge_json_data, overwrite, render_jinja


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def test_jinja_file_is_rendered_and_suffix_dropped(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "agent.md.jinja", "harness={{ harness }}\n")

    render_jinja(src, dst, {"harness": "claude"})

    assert not (dst / "agent.md.jinja").exists()
    assert (dst / "agent.md").read_bytes() == b"harness=claude\n"


def test_other_files_are_copied_byte_for_byte(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "plain.md", "{{ not_a_variable }}\r\n")
    blob = bytes(range(256))
    (src / "logo.png").write_bytes(blob)

    render_jinja(src, dst, {})

    assert (dst / "plain.md").read_bytes() == b"{{ not_a_variable }}\r\n"
    assert (dst / "logo.png").read_bytes() == blob


def test_nested_directories_are_recreated(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "skills" / "evolve" / "SKILL.md.jinja", "x\n")
    _write(src / "plugins" / "bridge.ts", "y\n")

    render_jinja(src, dst, {})

    assert (dst / "skills" / "evolve" / "SKILL.md").read_text(encoding="utf-8") == "x\n"
    assert (dst / "plugins" / "bridge.ts").read_text(encoding="utf-8") == "y\n"


def test_undefined_variable_raises(tmp_path):
    src = tmp_path / "src"
    _write(src / "a.md.jinja", "{{ tool.reed }}\n")

    with pytest.raises(UndefinedError):
        render_jinja(src, tmp_path / "dst", {"tool": {"read": "Read"}})


def test_block_tags_leave_no_blank_lines(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "a.md.jinja", 'before\n    {% if harness == "claude" %}\nclaude only\n    {% endif %}\nafter\n')

    render_jinja(src, dst, {"harness": "claude"})
    assert (dst / "a.md").read_text(encoding="utf-8") == "before\nclaude only\nafter\n"

    render_jinja(src, dst, {"harness": "opencode"})
    assert (dst / "a.md").read_text(encoding="utf-8") == "before\nafter\n"


def test_rendered_output_keeps_trailing_newline_and_uses_lf(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "a.md.jinja", "line1\r\nline2\r\n")

    render_jinja(src, dst, {})

    assert (dst / "a.md").read_bytes() == b"line1\nline2\n"


def test_existing_files_not_in_template_are_kept(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "a.md", "new\n")
    _write(dst / "a.md", "old\n")
    _write(dst / "user-skill" / "SKILL.md", "mine\n")

    render_jinja(src, dst, {})

    assert (dst / "a.md").read_text(encoding="utf-8") == "new\n"
    assert (dst / "user-skill" / "SKILL.md").read_text(encoding="utf-8") == "mine\n"


def test_returns_written_files(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "a.md.jinja", "a\n")
    _write(src / "sub" / "b.txt", "b\n")

    written = render_jinja(src, dst, {})

    assert sorted(written) == sorted([dst / "a.md", dst / "sub" / "b.txt"])


# ---- merge_json_data ----


def test_merge_objects_keeps_user_keys():
    user = {"mcpServers": {"github": {"command": "gh"}}, "theme": "dark"}
    template = {"mcpServers": {"evotrias": {"command": "python"}}}

    assert merge_json_data(user, template) == {
        "mcpServers": {"github": {"command": "gh"}, "evotrias": {"command": "python"}},
        "theme": "dark",
    }


def test_merge_arrays_appends_missing_elements_only():
    user = {"allow": ["Bash(ls *)", "Skill(evolve)"]}
    template = {"allow": ["Skill(evolve)", "mcp__evotrias__*"]}

    assert merge_json_data(user, template) == {"allow": ["Bash(ls *)", "Skill(evolve)", "mcp__evotrias__*"]}


def test_merge_arrays_dedupes_equal_objects():
    hook = {"matcher": "*", "hooks": [{"type": "command", "command": "python -m evotrias.hooks claude pre_tool_use"}]}
    mine = {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-check"}]}

    assert merge_json_data({"PreToolUse": [mine, hook]}, {"PreToolUse": [hook]}) == {"PreToolUse": [mine, hook]}


@pytest.mark.parametrize(
    ("user", "template"),
    [("ask", "allow"), ({"bash": "ask"}, "allow"), ("x", {"k": 1}), ([1], {"k": 1})],
    ids=["scalar", "object-to-scalar", "scalar-to-object", "array-to-object"],
)
def test_merge_scalar_or_type_mismatch_takes_template(user, template):
    assert merge_json_data({"permission": user}, {"permission": template}) == {"permission": template}


def test_merge_does_not_modify_inputs():
    user = {"a": {"b": [1]}}
    template = {"a": {"b": [2], "c": 3}}

    merge_json_data(user, template)

    assert user == {"a": {"b": [1]}}
    assert template == {"a": {"b": [2], "c": 3}}


# ---- merge_json / overwrite / STRATEGIES ----


def test_merge_json_creates_missing_file(tmp_path, log):
    src, dst = tmp_path / "t.json", tmp_path / "ws" / "settings.json"
    _write(src, '{"k": [1]}')

    assert merge_json(src, dst, {}) == [dst]
    assert json.loads(dst.read_text(encoding="utf-8")) == {"k": [1]}


def test_merge_json_keeps_user_key_order(tmp_path, log):
    src, dst = tmp_path / "t.json", tmp_path / "settings.json"
    _write(src, '{"a": 1}')
    _write(dst, '{"z": 0, "b": 0}')

    merge_json(src, dst, {})

    assert list(json.loads(dst.read_text(encoding="utf-8"))) == ["z", "b", "a"]


def test_merge_json_skips_write_when_nothing_changes(tmp_path, log):
    src, dst = tmp_path / "t.json", tmp_path / "settings.json"
    _write(src, '{"a": [1]}')
    _write(dst, '{"a": [1, 2]}')

    assert merge_json(src, dst, {}) == []
    assert dst.read_text(encoding="utf-8") == '{"a": [1, 2]}'


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", ""], ids=["invalid", "top-level-array", "empty"])
def test_merge_json_replaces_unusable_user_file(tmp_path, log, content):
    src, dst = tmp_path / "t.json", tmp_path / "settings.json"
    _write(src, '{"a": 1}')
    _write(dst, content)

    assert merge_json(src, dst, {}) == [dst]
    assert json.loads(dst.read_text(encoding="utf-8")) == {"a": 1}
    assert log.warnings  # the user's content is dropped, so say so in the log


def test_overwrite_copies_bytes(tmp_path):
    src, dst = tmp_path / "bridge.ts", tmp_path / "ws" / "plugins" / "bridge.ts"
    src.write_bytes(b"line\r\n")
    _write(dst, "old\n")

    assert overwrite(src, dst, {}) == [dst]
    assert dst.read_bytes() == b"line\r\n"


def test_every_install_mode_has_a_strategy():
    assert set(STRATEGIES) == set(InstallMode)

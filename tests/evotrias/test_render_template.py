"""Tests for evotrias.setup.render_template."""

from __future__ import annotations

import pytest
from jinja2 import UndefinedError

from evotrias.setup.render_template import render_tree


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def test_jinja_file_is_rendered_and_suffix_dropped(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "agent.md.jinja", "harness={{ harness }}\n")

    render_tree(src, dst, {"harness": "claude"})

    assert not (dst / "agent.md.jinja").exists()
    assert (dst / "agent.md").read_bytes() == b"harness=claude\n"


def test_other_files_are_copied_byte_for_byte(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "plain.md", "{{ not_a_variable }}\r\n")
    blob = bytes(range(256))
    (src / "logo.png").write_bytes(blob)

    render_tree(src, dst, {})

    assert (dst / "plain.md").read_bytes() == "{{ not_a_variable }}\r\n".encode("utf-8")
    assert (dst / "logo.png").read_bytes() == blob


def test_nested_directories_are_recreated(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "skills" / "evolve" / "SKILL.md.jinja", "x\n")
    _write(src / "plugins" / "bridge.ts", "y\n")

    render_tree(src, dst, {})

    assert (dst / "skills" / "evolve" / "SKILL.md").read_text(encoding="utf-8") == "x\n"
    assert (dst / "plugins" / "bridge.ts").read_text(encoding="utf-8") == "y\n"


def test_undefined_variable_raises(tmp_path):
    src = tmp_path / "src"
    _write(src / "a.md.jinja", "{{ tool.reed }}\n")

    with pytest.raises(UndefinedError):
        render_tree(src, tmp_path / "dst", {"tool": {"read": "Read"}})


def test_block_tags_leave_no_blank_lines(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "a.md.jinja", 'before\n    {% if harness == "claude" %}\nclaude only\n    {% endif %}\nafter\n')

    render_tree(src, dst, {"harness": "claude"})
    assert (dst / "a.md").read_text(encoding="utf-8") == "before\nclaude only\nafter\n"

    render_tree(src, dst, {"harness": "opencode"})
    assert (dst / "a.md").read_text(encoding="utf-8") == "before\nafter\n"


def test_rendered_output_keeps_trailing_newline_and_uses_lf(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "a.md.jinja", "line1\r\nline2\r\n")

    render_tree(src, dst, {})

    assert (dst / "a.md").read_bytes() == b"line1\nline2\n"


def test_existing_files_not_in_template_are_kept(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "a.md", "new\n")
    _write(dst / "a.md", "old\n")
    _write(dst / "user-skill" / "SKILL.md", "mine\n")

    render_tree(src, dst, {})

    assert (dst / "a.md").read_text(encoding="utf-8") == "new\n"
    assert (dst / "user-skill" / "SKILL.md").read_text(encoding="utf-8") == "mine\n"


def test_returns_written_files(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "a.md.jinja", "a\n")
    _write(src / "sub" / "b.txt", "b\n")

    written = render_tree(src, dst, {})

    assert sorted(written) == sorted([dst / "a.md", dst / "sub" / "b.txt"])

# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Tests for evotrias.setup.install: the install strategies, the JSON merge rules, and a workspace
set up end to end for each harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jinja2 import UndefinedError

from evotrias.enums_and_consts import InstallMode
from evotrias.setup.harness.claude import ClaudeHarness
from evotrias.setup.harness.opencode import OpencodeHarness
from evotrias.setup.harness.registry import HARNESSES
from evotrias.setup.install import (
    STRATEGIES,
    TEMPLATES,
    install,
    merge_json,
    merge_json_data,
    overwrite,
    render_jinja,
)

# ---- strategies: render_jinja ----


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


def test_renders_nested_directories(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _write(src / "a.md.jinja", "a\n")
    _write(src / "sub" / "b.txt", "b\n")

    render_jinja(src, dst, {})

    assert sorted(p.relative_to(dst).as_posix() for p in dst.rglob("*") if p.is_file()) == ["a.md", "sub/b.txt"]


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

    merge_json(src, dst, {})

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
    mtime = dst.stat().st_mtime_ns

    merge_json(src, dst, {})

    assert dst.read_text(encoding="utf-8") == '{"a": [1, 2]}'
    assert dst.stat().st_mtime_ns == mtime


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", ""], ids=["invalid", "top-level-array", "empty"])
def test_merge_json_replaces_unusable_user_file(tmp_path, log, content):
    src, dst = tmp_path / "t.json", tmp_path / "settings.json"
    _write(src, '{"a": 1}')
    _write(dst, content)

    merge_json(src, dst, {})

    assert json.loads(dst.read_text(encoding="utf-8")) == {"a": 1}
    assert log.warnings  # the user's content is dropped, so say so in the log


def test_overwrite_copies_bytes(tmp_path):
    src, dst = tmp_path / "bridge.ts", tmp_path / "ws" / "plugins" / "bridge.ts"
    src.write_bytes(b"line\r\n")
    _write(dst, "old\n")

    overwrite(src, dst, {})

    assert dst.read_bytes() == b"line\r\n"


def test_every_install_mode_has_a_strategy():
    assert set(STRATEGIES) == set(InstallMode)


# ---- install ----

SHARED_FILES = [
    "agents/evotrias-init-explorer.md",
    "agents/evotrias-meta-guidance-generator.md",
    "agents/evotrias-task-executor.md",
    "skills/evolve/SKILL.md",
    "skills/evotrias-init-explore/SKILL.md",
    "skills/evotrias-task-exec/SKILL.md",
]
EXPECTED_FILES = {
    "claude": {
        *(f".claude/{rel}" for rel in SHARED_FILES),
        ".claude/settings.json",
        ".mcp.json",
        ".evotrias/active_harness",
    },
    "opencode": {
        *(f".opencode/{rel}" for rel in SHARED_FILES),
        "opencode.json",
        ".opencode/plugins/evotrias-bridge.ts",
        ".evotrias/active_harness",
    },
}


@pytest.fixture(autouse=True)
def _quiet(log, monkeypatch, tmp_path):
    # keep evotrias.logger / any cwd-relative path away from the repository
    monkeypatch.chdir(tmp_path)


def _install(harness, workspace: Path) -> None:
    """Run the install generator to the end."""
    for _step in install(harness, workspace):
        pass


def _files(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


def _snapshot(root: Path) -> dict[str, bytes]:
    return {rel: (root / rel).read_bytes() for rel in _files(root)}


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _template_json(rel: str) -> dict:
    return json.loads((TEMPLATES / rel).read_text(encoding="utf-8"))


@pytest.mark.parametrize("harness", HARNESSES, ids=lambda h: h.name)
def test_install_into_empty_workspace(harness, tmp_path):
    workspace = tmp_path / "ws"

    _install(harness, workspace)

    assert _files(workspace) == EXPECTED_FILES[harness.name]
    assert (workspace / ".evotrias/active_harness").read_text(encoding="utf-8") == f"{harness.name}\n"


@pytest.mark.parametrize("harness", HARNESSES, ids=lambda h: h.name)
def test_agents_are_rendered_for_the_harness(harness, tmp_path):
    _install(harness, tmp_path)

    agents_dir = next(dst for src, dst, _mode in harness.install_entries if src == "agents")
    text = (tmp_path / agents_dir / "evotrias-task-executor.md").read_text(encoding="utf-8")
    metadata = yaml.safe_load(text.split("---")[1])
    assert set(metadata) == set(harness.agent_frontmatter_fields(name="n", description="d"))


def test_claude_config_files_equal_templates_in_empty_workspace(tmp_path):
    _install(ClaudeHarness(), tmp_path)

    assert _json(tmp_path / ".claude/settings.json") == _template_json("claude/settings.json")
    assert _json(tmp_path / ".mcp.json") == _template_json("claude/mcp.json")


def test_opencode_files_equal_templates_in_empty_workspace(tmp_path):
    _install(OpencodeHarness(), tmp_path)

    assert _json(tmp_path / "opencode.json") == _template_json("opencode/opencode.json")
    bridge = (TEMPLATES / "opencode/plugins/evotrias-bridge.ts").read_bytes()
    assert (tmp_path / ".opencode/plugins/evotrias-bridge.ts").read_bytes() == bridge


def test_claude_install_keeps_user_config(tmp_path):
    user_hook = {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-check"}]}
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.json").write_text(
        json.dumps(
            {
                "model": "opus",
                "permissions": {"allow": ["Bash(ls *)"], "deny": ["Read(.env)"]},
                "hooks": {"PreToolUse": [user_hook]},
                "enabledMcpjsonServers": ["github"],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"github": {"command": "gh"}}}), encoding="utf-8")

    _install(ClaudeHarness(), tmp_path)

    settings = _json(tmp_path / ".claude/settings.json")
    template = _template_json("claude/settings.json")
    assert settings["model"] == "opus"
    assert settings["permissions"]["deny"] == ["Read(.env)"]
    assert settings["permissions"]["allow"] == ["Bash(ls *)", *template["permissions"]["allow"]]
    assert settings["hooks"]["PreToolUse"] == [user_hook, *template["hooks"]["PreToolUse"]]
    assert settings["enabledMcpjsonServers"] == ["github", "evotrias"]
    assert set(_json(tmp_path / ".mcp.json")["mcpServers"]) == {"github", "evotrias"}


def test_opencode_install_keeps_user_config_but_forces_permission(tmp_path):
    (tmp_path / "opencode.json").write_text(
        json.dumps({"model": "x/y", "permission": {"bash": "ask"}, "mcp": {"other": {"type": "remote"}}}),
        encoding="utf-8",
    )

    _install(OpencodeHarness(), tmp_path)

    config = _json(tmp_path / "opencode.json")
    assert config["model"] == "x/y"
    assert config["permission"] == "allow"
    assert set(config["mcp"]) == {"other", "evotrias"}


@pytest.mark.parametrize("harness", HARNESSES, ids=lambda h: h.name)
def test_install_leaves_user_files_alone(harness, tmp_path):
    problem_skill = tmp_path / harness.skills_dir / "circle-packing" / "SKILL.md"
    problem_skill.parent.mkdir(parents=True)
    problem_skill.write_text("mine\n", encoding="utf-8")
    notes = tmp_path / "notes.txt"
    notes.write_text("keep\n", encoding="utf-8")

    _install(harness, tmp_path)

    assert problem_skill.read_text(encoding="utf-8") == "mine\n"
    assert notes.read_text(encoding="utf-8") == "keep\n"


def test_unusable_user_json_is_replaced(tmp_path):
    (tmp_path / ".mcp.json").write_text("{ // comments are not JSON\n}", encoding="utf-8")

    _install(ClaudeHarness(), tmp_path)

    assert _json(tmp_path / ".mcp.json") == _template_json("claude/mcp.json")


@pytest.mark.parametrize("harness", HARNESSES, ids=lambda h: h.name)
def test_install_twice_changes_nothing(harness, tmp_path):
    _install(harness, tmp_path)
    before = _snapshot(tmp_path)
    merged = [tmp_path / dst for _src, dst, mode in harness.install_entries if mode == InstallMode.MERGE_JSON]
    mtimes = [path.stat().st_mtime_ns for path in merged]

    _install(harness, tmp_path)

    assert _snapshot(tmp_path) == before
    # merged JSON is not even rewritten when nothing changes
    assert [path.stat().st_mtime_ns for path in merged] == mtimes


def test_switching_harness_keeps_the_previous_one(tmp_path):
    _install(ClaudeHarness(), tmp_path)
    claude_files = {rel: data for rel, data in _snapshot(tmp_path).items() if not rel.startswith(".evotrias/")}

    _install(OpencodeHarness(), tmp_path)

    after = _snapshot(tmp_path)
    assert {rel: after[rel] for rel in claude_files} == claude_files
    assert after[".evotrias/active_harness"] == b"opencode\n"


@pytest.mark.parametrize("harness", HARNESSES, ids=lambda h: h.name)
def test_install_steps_one_entry_at_a_time(harness, tmp_path):
    steps = install(harness, tmp_path)

    assert list(tmp_path.iterdir()) == []  # lazy: nothing happens before iterating
    assert list(steps) == [dst for _src, dst, _mode in harness.install_entries] + [".evotrias/active_harness"]

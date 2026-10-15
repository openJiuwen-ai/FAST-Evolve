"""Tests for evotrias.setup.install: a workspace set up end to end for each harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from evotrias.enums_and_consts import InstallMode
from evotrias.harness.claude import ClaudeHarness
from evotrias.harness.opencode import OpencodeHarness
from evotrias.harness.registry import HARNESSES
from evotrias.setup.install import TEMPLATES, install

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


def _install(harness, workspace: Path) -> list[Path]:
    """Run the install generator to the end; the files written by every step."""
    return [path for _label, written in install(harness, workspace) for path in written]


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

    written = _install(harness, workspace)

    assert _files(workspace) == EXPECTED_FILES[harness.name]
    assert {path.relative_to(workspace).as_posix() for path in written} == EXPECTED_FILES[harness.name]
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

    written = _install(harness, tmp_path)

    assert _snapshot(tmp_path) == before
    merged = {dst for _src, dst, mode in harness.install_entries if mode == InstallMode.MERGE_JSON}
    assert merged.isdisjoint(path.relative_to(tmp_path).as_posix() for path in written)


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
    labels = [label for label, _written in steps]
    assert labels == [dst for _src, dst, _mode in harness.install_entries] + [".evotrias/active_harness"]

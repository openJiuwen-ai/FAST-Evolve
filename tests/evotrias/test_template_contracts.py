"""Contracts of the shipped templates: agents / skills as each harness renders them, plus the harness-specific files."""

from __future__ import annotations

import asyncio
import importlib
import json
import pkgutil
import re
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml
from fastmcp import Client

import evotrias.hooks.claude
import evotrias.hooks.opencode
from evotrias.enums_and_consts import MCP_SERVER_NAME, InstallMode
from evotrias.harness.base import Harness
from evotrias.harness.claude import ClaudeHarness
from evotrias.harness.opencode import OpencodeHarness
from evotrias.hooks import broker
from evotrias.mcp import mcp, public_tools
from evotrias.setup.install_modes import render_jinja

TEMPLATES = files("evotrias") / "templates"
HARNESSES = [ClaudeHarness(), OpencodeHarness()]

TEMPLATE_FILES = {
    "agents/evotrias-init-explorer.md.jinja",
    "agents/evotrias-meta-guidance-generator.md.jinja",
    "agents/evotrias-task-executor.md.jinja",
    "skills/evolve/SKILL.md.jinja",
    "skills/evotrias-task-exec/SKILL.md.jinja",
    "skills/evotrias-init-explore/SKILL.md",
    "claude/settings.json",
    "claude/mcp.json",
    "opencode/opencode.json",
    "opencode/plugins/evotrias-bridge.ts",
}
AGENT_NAMES = ("evotrias-init-explorer", "evotrias-meta-guidance-generator", "evotrias-task-executor")
MASTER_SKILL = "skills/evolve/SKILL.md"
TASK_SKILL = "skills/evotrias-task-exec/SKILL.md"

PUBLIC_TOOL_NAMES = {
    "configure_run",
    "select_parent",
    "select_inspirations",
    "record_attempt",
    "get_notes_prompt",
    "commit_generated_note",
    "get_meta_guidance_request",
    "commit_meta_guidance",
    "get_meta_guidance_prompt",
    "check_convergence",
    "finalize_run",
}
PYTHON_WRAPPER_NAMES = {f"evolve_{tool_name}": tool_name for tool_name in PUBLIC_TOOL_NAMES}
TASK_MCP_TOOLS = (
    "select_parent",
    "select_inspirations",
    "get_notes_prompt",
    "get_meta_guidance_prompt",
    "record_attempt",
    "commit_generated_note",
)


class Rendered(NamedTuple):
    harness: Harness
    root: Path

    def text(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    def agent(self, name: str) -> tuple[dict[str, object], str]:
        text = self.text(f"agents/{name}.md")
        assert text.startswith("---\n")
        _, raw_frontmatter, body = text.split("---", 2)
        metadata = yaml.safe_load(raw_frontmatter)
        assert isinstance(metadata, dict)
        return metadata, body


@pytest.fixture(scope="module", params=HARNESSES, ids=lambda h: h.name)
def rendered(request, tmp_path_factory) -> Rendered:
    harness = request.param
    root = tmp_path_factory.mktemp(harness.name)
    # only the shared trees; claude/ and opencode/ hold harness-specific files
    for tree in ("agents", "skills"):
        render_jinja(TEMPLATES / tree, root / tree, harness.template_context())
    return Rendered(harness, root)


def _walk(node: Traversable, prefix: str = "") -> set[str]:
    found: set[str] = set()
    for child in node.iterdir():
        rel = f"{prefix}{child.name}"
        found |= _walk(child, f"{rel}/") if child.is_dir() else {rel}
    return found


def _dispatch_template(skill_text: str, agent_name: str) -> str:
    marker = f"**Template for {agent_name} dispatch:**"
    after_marker = skill_text.split(marker, 1)[1]
    return after_marker.split("```", 2)[1].strip()


async def _protocol_tools():
    async with Client(mcp) as client:
        return await client.list_tools()


def test_registered_mcp_tools_use_one_complete_compact_namespace():
    registered = {tool.name for tool in asyncio.run(_protocol_tools())}

    assert registered == PUBLIC_TOOL_NAMES
    assert all(not name.startswith("evolve_") for name in registered)


def test_legacy_python_wrapper_names_map_to_compact_mcp_names():
    registered = {tool.name: tool.fn for tool in asyncio.run(mcp.list_tools())}

    for wrapper_name, mcp_name in PYTHON_WRAPPER_NAMES.items():
        wrapper = getattr(public_tools, wrapper_name)
        assert registered[mcp_name] is wrapper


def test_note_and_meta_commit_tools_keep_distinct_schemas():
    registered = {tool.name: tool.input_schema for tool in asyncio.run(_protocol_tools())}

    note_fields = set(registered["commit_generated_note"]["properties"])
    meta_fields = set(registered["commit_meta_guidance"]["properties"])
    request_fields = set(registered["get_meta_guidance_request"]["properties"])

    assert note_fields == {"run_id", "note_request_id", "generated_note_json"}
    assert meta_fields == {"run_id", "request_id", "guidance_json"}
    assert request_fields == {"run_id", "request_id"}


@pytest.mark.parametrize("harness", HARNESSES, ids=lambda h: h.name)
def test_mcp_full_names_fit_tool_name_limit(harness):
    # MCP clients reject tool names longer than 64 characters.
    assert max(len(harness.mcp(name)) for name in PUBLIC_TOOL_NAMES) <= 64


def test_template_tree_lists_expected_files():
    assert _walk(TEMPLATES) == TEMPLATE_FILES


def test_agent_frontmatter_matches_harness(rendered):
    for name in AGENT_NAMES:
        metadata, _ = rendered.agent(name)

        if rendered.harness.name == "claude":
            assert set(metadata) == {"name", "description"}
            assert metadata["name"] == name
        else:
            # opencode names the agent after its file
            assert set(metadata) == {"description", "mode"}
            assert metadata["mode"] == "subagent"
        assert metadata["description"]


def test_init_agent_uses_init_explore_skill(rendered):
    _, body = rendered.agent("evotrias-init-explorer")

    assert "`evotrias-init-explore`" in body


def test_task_agent_uses_task_exec_skill(rendered):
    _, body = rendered.agent("evotrias-task-executor")

    assert "`evotrias-task-exec`" in body

    task_skill = rendered.text(TASK_SKILL)
    for tool_name in TASK_MCP_TOOLS:
        assert f"`{tool_name}`" in task_skill


def test_meta_guidance_agent_is_stateless(rendered):
    _, body = rendered.agent("evotrias-meta-guidance-generator")

    assert "do not poll" in body.lower()
    assert "never modify project files" in body.lower()
    assert "inspect verifier/evaluator source" not in body.lower()
    assert "evaluator are read-only evidence" not in body.lower()
    assert body.index("get_meta_guidance_request") < body.index("commit_meta_guidance")
    assert "Never return the guidance JSON to the orchestrator." in body
    assert "make at most one additional commit attempt" in body


def test_meta_guidance_agent_returns_only_the_compact_result_contract(rendered):
    _, body = rendered.agent("evotrias-meta-guidance-generator")

    assert ("meta_guidance_result: committed request_id=<request_id> " "guidance_count=<count>") in body
    assert ("meta_guidance_result: not_committed request_id=<request_id> " "reason=<reason_code>") in body
    assert ("meta_guidance_result: mcp_error request_id=<request_id> " "reason=<short_reason_code>") in body
    assert "Do not include the request payload, generated guidance" in body


def test_orchestrator_passes_only_ids_and_never_handles_guidance_payload(rendered):
    master = rendered.text(MASTER_SKILL)

    assert _dispatch_template(master, "evotrias-meta-guidance-generator") == (
        "run_id: <run_id>\nrequest_id: <request_id>"
    )
    assert "get_meta_guidance_request" not in master
    assert "commit_meta_guidance" not in master
    assert "current_best_assessment" not in master
    assert "passes only `run_id` and `request_id`" in master


def test_orchestrator_uses_aligned_evotrias_dashboard_banner(rendered):
    master = rendered.text(MASTER_SKILL)

    assert "┌─ 🦖 EvoTrias UI" in master
    assert "└─ 🖥️  <dashboard.url>" in master


def test_orchestrator_dispatches_project_level_agent_names(rendered):
    master = rendered.text(MASTER_SKILL)

    for agent_name in AGENT_NAMES:
        assert agent_name in master


def test_orchestrator_mentions_only_orchestration_mcp_tools(rendered):
    master = rendered.text(MASTER_SKILL)

    for tool_name in (
        "configure_run",
        "check_convergence",
        "finalize_run",
    ):
        assert tool_name in master

    for worker_only_tool in (
        *TASK_MCP_TOOLS,
        "get_meta_guidance_request",
        "commit_meta_guidance",
    ):
        assert worker_only_tool not in master


def test_meta_worker_is_foreground_reconciled_before_replenishment(rendered):
    master = rendered.text(MASTER_SKILL)
    scheduling = master.split("### Scheduling Pseudocode", 1)[1]

    request_branch = scheduling.index('c.action == "request_meta_guidance"')
    dispatch_position = scheduling.index("Dispatch to evotrias-meta-guidance-generator", request_branch)
    wait_position = scheduling.index("Wait for the evotrias-meta-guidance-generator compact result", dispatch_position)
    just_after_meta_position = scheduling.index("just_after_meta = true", wait_position)
    continue_position = scheduling.index("// re-check convergence", just_after_meta_position)
    replenish_position = scheduling.index('action == "continue"', continue_position)

    assert request_branch < dispatch_position < wait_position
    assert wait_position < just_after_meta_position < continue_position
    assert continue_position < replenish_position
    assert f"`{rendered.harness.background_arg}: false`" in scheduling


def test_rendered_templates_have_no_legacy_names(rendered):
    legacy = ["evotrias_plugin", "mcp__plugin_", "evolve-task-exec", "`init-explore`", "{{", "{%"]
    if rendered.harness.name != "claude":
        legacy.append("run_in_background")
    # agent names without the evotrias- prefix
    bare_agent = re.compile(r"(?<![\w-])(init-explorer|task-executor|meta-guidance-generator)")

    for path in rendered.root.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert [token for token in legacy if token in text] == [], path
            assert bare_agent.findall(text) == [], path


def test_prose_tool_names_follow_harness(rendered):
    task_skill = rendered.text(TASK_SKILL)
    tool = rendered.harness.tool_names

    assert f"create that JSON file with the {tool['write']} tool, not {tool['bash']}." in task_skill
    assert f"the {tool['bash']} tool already reports the command exit code" in task_skill


# ---- harness-specific files: claude/ and opencode/ ----


def _json(rel: str) -> dict:
    return json.loads((TEMPLATES / rel).read_text(encoding="utf-8"))


@pytest.fixture
def registered_events():
    """Events each harness registers, from a freshly re-imported registry (other tests clear it)."""
    saved = {key: list(entries) for key, entries in broker._handlers.items()}
    broker._handlers.clear()
    for package in (evotrias.hooks.claude, evotrias.hooks.opencode):
        for info in pkgutil.iter_modules(package.__path__, f"{package.__name__}."):
            importlib.reload(importlib.import_module(info.name))
    events: dict[str, set[str]] = {}
    for harness, event in broker._handlers:
        events.setdefault(harness, set()).add(event)
    yield events
    broker._handlers.clear()
    broker._handlers.update(saved)


def test_claude_hooks_cover_registered_events(registered_events):
    hooks = _json("claude/settings.json")["hooks"]
    events = set()
    for claude_event, entries in hooks.items():
        event = re.sub(r"(?<!^)(?=[A-Z])", "_", claude_event).lower()
        assert entries == [
            {"matcher": "*", "hooks": [{"type": "command", "command": f"python -m evotrias.hooks claude {event}"}]}
        ]
        events.add(event)

    assert events == registered_events["claude"]


def test_opencode_bridge_forwards_registered_events(registered_events):
    bridge = (TEMPLATES / "opencode/plugins/evotrias-bridge.ts").read_text(encoding="utf-8")

    assert 'Bun.spawn(["python", "-m", "evotrias.hooks", "opencode", event]' in bridge
    for event in registered_events["opencode"]:
        assert f'"{event}"' in bridge


def test_claude_allow_rules_match_templates():
    allow = _json("claude/settings.json")["permissions"]["allow"]
    skills = {child.name for child in (TEMPLATES / "skills").iterdir()}
    agents = {child.name.removesuffix(".md.jinja") for child in (TEMPLATES / "agents").iterdir()}

    assert sorted(allow) == sorted(
        [f"Skill({name})" for name in skills] + [f"Agent({name})" for name in agents] + [ClaudeHarness().mcp("*")]
    )


def test_mcp_server_configs_launch_the_package_server():
    claude_servers = _json("claude/mcp.json")["mcpServers"]
    opencode_servers = _json("opencode/opencode.json")["mcp"]

    assert claude_servers == {MCP_SERVER_NAME: {"command": "python", "args": ["-m", "evotrias.mcp"]}}
    assert opencode_servers == {
        MCP_SERVER_NAME: {"type": "local", "command": ["python", "-m", "evotrias.mcp"], "enabled": True}
    }
    assert _json("claude/settings.json")["enabledMcpjsonServers"] == [MCP_SERVER_NAME]


def test_harness_files_have_no_repo_checkout_leftovers():
    # uv / PYTHONPATH / hooks_entry assumed the workspace lived inside the repository
    leftovers = re.compile(r"\buv\b|PYTHONPATH|hooks_entry")
    for tree in ("claude", "opencode"):
        for rel in _walk(TEMPLATES / tree, f"{tree}/"):
            assert leftovers.findall((TEMPLATES / rel).read_text(encoding="utf-8")) == [], rel


@pytest.mark.parametrize("harness", HARNESSES, ids=lambda h: h.name)
def test_install_entries_cover_shared_and_own_templates(harness):
    covered: set[str] = set()
    for src, _dst, mode in harness.install_entries:
        node = TEMPLATES / src
        if mode == InstallMode.RENDER_JINJA:
            assert node.is_dir(), src  # render_jinja walks a directory
        covered |= _walk(node, f"{src}/") if node.is_dir() else {src}

    own = {rel for rel in TEMPLATE_FILES if rel.split("/")[0] in ("agents", "skills", harness.name)}
    assert covered == own

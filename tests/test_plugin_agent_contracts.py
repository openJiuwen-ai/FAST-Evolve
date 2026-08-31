from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess

import yaml
from fastmcp import Client

from evotrias.mcp import mcp, public_tools

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "agent_evolve_plugin"
MASTER_SKILL = PLUGIN_ROOT / "skills" / "evolve" / "SKILL.md"
TASK_SKILL = PLUGIN_ROOT / "skills" / "evolve-task-exec" / "SKILL.md"
INIT_AGENT = PLUGIN_ROOT / "agents" / "init-explorer.md"
TASK_AGENT = PLUGIN_ROOT / "agents" / "task-executor.md"
META_AGENT = PLUGIN_ROOT / "agents" / "meta-guidance-generator.md"
PLUGIN_MANIFEST = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
MCP_CONFIG = PLUGIN_ROOT / ".mcp.json"
PROJECT_SETTINGS = REPO_ROOT / ".claude" / "settings.json"
SHARED_WORKSPACE_HOOK = (
    REPO_ROOT / ".claude" / "hooks" / "enforce-shared-evolve-agents.mjs"
)

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
PYTHON_WRAPPER_NAMES = {
    f"evolve_{tool_name}": tool_name for tool_name in PUBLIC_TOOL_NAMES
}
MCP_SERVER_NAME = "ctrl"
MCP_TOOL_PREFIX = "mcp__plugin_agent_evolve_plugin_ctrl__"
GET_REQUEST_TOOL = f"{MCP_TOOL_PREFIX}get_meta_guidance_request"
COMMIT_GUIDANCE_TOOL = f"{MCP_TOOL_PREFIX}commit_meta_guidance"

INIT_BUILTIN_TOOLS = {"Skill", "Read", "Write", "Glob", "Grep", "Bash"}
TASK_BUILTIN_TOOLS = INIT_BUILTIN_TOOLS | {"Edit"}
TASK_MCP_TOOLS = {
    f"{MCP_TOOL_PREFIX}select_parent",
    f"{MCP_TOOL_PREFIX}select_inspirations",
    f"{MCP_TOOL_PREFIX}get_notes_prompt",
    f"{MCP_TOOL_PREFIX}get_meta_guidance_prompt",
    f"{MCP_TOOL_PREFIX}record_attempt",
    f"{MCP_TOOL_PREFIX}commit_generated_note",
}
META_TOOLS = {"Read", "Glob", "Grep", GET_REQUEST_TOOL, COMMIT_GUIDANCE_TOOL}


def _agent_parts(path: Path) -> tuple[dict[str, object], str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, raw_frontmatter, body = text.split("---", 2)
    metadata = yaml.safe_load(raw_frontmatter)
    assert isinstance(metadata, dict)
    return metadata, body


def _agent_tools(path: Path) -> set[str]:
    metadata, _ = _agent_parts(path)
    return {item.strip() for item in str(metadata["tools"]).split(",")}


def _dispatch_template(skill_text: str, agent_name: str) -> str:
    marker = f"**Template for {agent_name} dispatch:**"
    after_marker = skill_text.split(marker, 1)[1]
    return after_marker.split("```", 2)[1].strip()


def _settings() -> dict[str, object]:
    return json.loads(PROJECT_SETTINGS.read_text(encoding="utf-8"))


async def _protocol_tools():
    async with Client(mcp) as client:
        return await client.list_tools()


def test_registered_mcp_tools_use_one_complete_compact_namespace():
    registered = {tool.name for tool in asyncio.run(_protocol_tools())}

    assert registered == PUBLIC_TOOL_NAMES
    assert all(not name.startswith("evolve_") for name in registered)


def test_legacy_python_wrapper_names_map_to_compact_mcp_names():
    registered = {
        tool.name: tool.fn for tool in asyncio.run(mcp.list_tools())
    }

    for wrapper_name, mcp_name in PYTHON_WRAPPER_NAMES.items():
        wrapper = getattr(public_tools, wrapper_name)
        assert registered[mcp_name] is wrapper


def test_note_and_meta_commit_tools_keep_distinct_schemas():
    registered = {
        tool.name: tool.inputSchema for tool in asyncio.run(_protocol_tools())
    }

    note_fields = set(registered["commit_generated_note"]["properties"])
    meta_fields = set(registered["commit_meta_guidance"]["properties"])
    request_fields = set(registered["get_meta_guidance_request"]["properties"])

    assert note_fields == {"run_id", "note_request_id", "generated_note_json"}
    assert meta_fields == {"run_id", "request_id", "guidance_json"}
    assert request_fields == {"run_id", "request_id"}


def test_complete_claude_tool_names_fit_without_hash_aliases():
    manifest = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
    mcp_config = json.loads(MCP_CONFIG.read_text(encoding="utf-8"))
    server_names = set(mcp_config["mcpServers"])

    assert mcp.name == "agent_evolve"
    assert manifest["name"] == "agent_evolve_plugin"
    assert server_names == {MCP_SERVER_NAME}

    complete_names = {
        f"mcp__plugin_{manifest['name']}_{MCP_SERVER_NAME}__{name}"
        for name in PUBLIC_TOOL_NAMES
    }
    assert max(map(len, complete_names)) <= 64
    assert max(complete_names, key=len) == GET_REQUEST_TOOL
    assert len(GET_REQUEST_TOOL) == 63

    allowed = set(_settings()["permissions"]["allow"])
    allowed_mcp_tools = {item for item in allowed if item.startswith("mcp__plugin_")}
    assert allowed_mcp_tools == complete_names


def test_init_agent_has_no_mcp_or_unneeded_mutation_tools():
    metadata, body = _agent_parts(INIT_AGENT)

    assert metadata["name"] == "init-explorer"
    assert metadata["model"] == "inherit"
    assert _agent_tools(INIT_AGENT) == INIT_BUILTIN_TOOLS
    assert "init-explore" in body


def test_task_agent_is_scoped_to_task_lifecycle_tools():
    metadata, body = _agent_parts(TASK_AGENT)
    tools = _agent_tools(TASK_AGENT)

    assert metadata["name"] == "task-executor"
    assert metadata["model"] == "inherit"
    assert tools == TASK_BUILTIN_TOOLS | TASK_MCP_TOOLS
    assert "evolve-task-exec" in body

    task_skill = TASK_SKILL.read_text(encoding="utf-8")
    for tool_name in (
        "select_parent",
        "select_inspirations",
        "get_notes_prompt",
        "get_meta_guidance_prompt",
        "record_attempt",
        "commit_generated_note",
    ):
        assert f"`{tool_name}`" in task_skill

    forbidden = {
        f"{MCP_TOOL_PREFIX}configure_run",
        GET_REQUEST_TOOL,
        COMMIT_GUIDANCE_TOOL,
        f"{MCP_TOOL_PREFIX}check_convergence",
        f"{MCP_TOOL_PREFIX}finalize_run",
    }
    assert tools.isdisjoint(forbidden)


def test_meta_guidance_agent_is_stateless_and_strictly_tool_scoped():
    metadata, body = _agent_parts(META_AGENT)

    assert metadata["name"] == "meta-guidance-generator"
    assert metadata["model"] == "inherit"
    assert _agent_tools(META_AGENT) == META_TOOLS
    assert "do not poll" in body.lower()
    assert "never modify project files" in body.lower()
    assert body.index("get_meta_guidance_request") < body.index("commit_meta_guidance")
    assert "Never return the guidance JSON to the orchestrator." in body
    assert "make at most one additional commit attempt" in body
    assert f"{MCP_TOOL_PREFIX}commit_generated_note" not in _agent_tools(META_AGENT)


def test_all_agent_mcp_tools_are_registered_and_permitted():
    allowed = set(_settings()["permissions"]["allow"])
    registered_complete_names = {
        f"{MCP_TOOL_PREFIX}{name}" for name in PUBLIC_TOOL_NAMES
    }

    agent_mcp_tools = set()
    for path in (INIT_AGENT, TASK_AGENT, META_AGENT):
        agent_mcp_tools.update(
            tool for tool in _agent_tools(path) if tool.startswith("mcp__")
        )

    assert agent_mcp_tools <= registered_complete_names
    assert agent_mcp_tools <= allowed


def test_meta_guidance_agent_returns_only_the_compact_result_contract():
    _, body = _agent_parts(META_AGENT)

    assert (
        "meta_guidance_result: committed request_id=<request_id> "
        "guidance_count=<count>"
    ) in body
    assert (
        "meta_guidance_result: not_committed request_id=<request_id> "
        "reason=<reason_code>"
    ) in body
    assert (
        "meta_guidance_result: mcp_error request_id=<request_id> "
        "reason=<short_reason_code>"
    ) in body
    assert "Do not include the request payload, generated guidance" in body


def test_orchestrator_passes_only_ids_and_never_handles_guidance_payload():
    master = MASTER_SKILL.read_text(encoding="utf-8")

    assert _dispatch_template(master, "meta-guidance-generator") == (
        "run_id: <run_id>\nrequest_id: <request_id>"
    )
    assert "get_meta_guidance_request" not in master
    assert "commit_meta_guidance" not in master
    assert "current_best_assessment" not in master
    assert "passes only `run_id` and `request_id`" in master


def test_orchestrator_uses_aligned_evotrias_dashboard_banner():
    master = MASTER_SKILL.read_text(encoding="utf-8")

    assert "┌─ 🦖 EvoTrias UI" in master
    assert "└─ 🖥️  <dashboard.url>" in master
    assert "🚀 AGENT EVOLVE UI" not in master


def test_orchestrator_forbids_worktree_isolation_for_persistent_workers():
    master = MASTER_SKILL.read_text(encoding="utf-8")

    for agent_name in (
        "agent_evolve_plugin:init-explorer",
        "agent_evolve_plugin:task-executor",
        "agent_evolve_plugin:meta-guidance-generator",
    ):
        assert agent_name in master
    assert "MUST NOT contain an `isolation` field" in master
    assert 'setting `"worktree"`, `"remote"`, or any other' in master
    assert "never to `.claude/worktrees/...`" in master


def test_shared_workspace_hook_strips_isolation_for_persistent_workers():
    settings = _settings()
    pre_tool_use_hooks = settings["hooks"]["PreToolUse"]

    assert pre_tool_use_hooks == [
        {
            "matcher": "Agent",
            "hooks": [
                {
                    "type": "command",
                    "command": "node .claude/hooks/enforce-shared-evolve-agents.mjs",
                }
            ],
        }
    ]

    payload = {
        "tool_name": "Agent",
        "tool_input": {
            "description": "Generate directions",
            "isolation": "worktree",
            "model": "sonnet",
            "prompt": "run_id: example",
            "subagent_type": "agent_evolve_plugin:init-explorer",
        },
    }

    result = subprocess.run(
        ["node", str(SHARED_WORKSPACE_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        check=True,
        text=True,
    )
    output = json.loads(result.stdout)

    assert "isolation" not in output["hookSpecificOutput"]["updatedInput"]
    assert output["hookSpecificOutput"]["updatedInput"]["prompt"] == "run_id: example"


def test_shared_workspace_hook_leaves_other_agents_unchanged():
    payload = {
        "tool_name": "Agent",
        "tool_input": {
            "isolation": "worktree",
            "subagent_type": "Explore",
        },
    }

    result = subprocess.run(
        ["node", str(SHARED_WORKSPACE_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        check=True,
        text=True,
    )

    assert result.stdout == ""


def test_orchestrator_mentions_only_orchestration_mcp_tools():
    master = MASTER_SKILL.read_text(encoding="utf-8")

    for tool_name in (
        "configure_run",
        "check_convergence",
        "finalize_run",
    ):
        assert tool_name in master

    for worker_only_tool in (
        "select_parent",
        "select_inspirations",
        "get_notes_prompt",
        "get_meta_guidance_prompt",
        "record_attempt",
        "commit_generated_note",
        "get_meta_guidance_request",
        "commit_meta_guidance",
    ):
        assert worker_only_tool not in master


def test_meta_worker_is_foreground_reconciled_before_replenishment():
    master = MASTER_SKILL.read_text(encoding="utf-8")
    scheduling = master.split("### Scheduling Pseudocode", 1)[1]

    request_branch = scheduling.index('c.action == "request_meta_guidance"')
    dispatch_position = scheduling.index("Dispatch to meta-guidance-generator", request_branch)
    wait_position = scheduling.index("Wait for the meta-guidance-generator compact result", dispatch_position)
    just_after_meta_position = scheduling.index("just_after_meta = true", wait_position)
    continue_position = scheduling.index("// re-check convergence", just_after_meta_position)
    replenish_position = scheduling.index('action == "continue"', continue_position)

    assert request_branch < dispatch_position < wait_position
    assert wait_position < just_after_meta_position < continue_position
    assert continue_position < replenish_position
    assert "`run_in_background: false`" in scheduling


def test_project_permissions_allow_dispatching_all_plugin_agents():
    allowed = set(_settings()["permissions"]["allow"])

    assert {
        "Agent(init-explorer)",
        "Agent(task-executor)",
        "Agent(meta-guidance-generator)",
    } <= allowed

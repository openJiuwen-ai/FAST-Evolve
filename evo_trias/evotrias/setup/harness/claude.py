# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
from __future__ import annotations

from evotrias.enums_and_consts import MCP_SERVER_NAME, InstallMode
from evotrias.setup.harness.base import Harness


class ClaudeHarness(Harness):
    name = "claude"
    tool_names = {
        "skill": "Skill",
        "read": "Read",
        "write": "Write",
        "edit": "Edit",
        "glob": "Glob",
        "grep": "Grep",
        "bash": "Bash",
        "webfetch": "WebFetch",
    }
    background_arg = "run_in_background"
    executable = "claude"
    default_args = {"--permission-mode": ("auto", {"--permission-mode", "--dangerously-skip-permissions"})}
    skills_dir = ".claude/skills"
    install_entries = [
        ("agents", ".claude/agents", InstallMode.RENDER_JINJA),
        ("skills", skills_dir, InstallMode.RENDER_JINJA),
        ("claude/settings.json", ".claude/settings.json", InstallMode.MERGE_JSON),
        ("claude/mcp.json", ".mcp.json", InstallMode.MERGE_JSON),
    ]

    def mcp(self, tool: str) -> str:
        return f"mcp__{MCP_SERVER_NAME}__{tool}"

    def agent_frontmatter_fields(self, **fields: str) -> dict[str, str]:
        return {
            "name": fields["name"],
            "description": fields["description"],
        }

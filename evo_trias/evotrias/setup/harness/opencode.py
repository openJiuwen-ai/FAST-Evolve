# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
from __future__ import annotations

from evotrias.enums_and_consts import MCP_SERVER_NAME, InstallMode
from evotrias.setup.harness.base import Harness


class OpencodeHarness(Harness):
    name = "opencode"
    tool_names = {
        "skill": "skill",
        "read": "read",
        "write": "write",
        "edit": "edit",
        "glob": "glob",
        "grep": "grep",
        "bash": "bash",
        "webfetch": "webfetch",
    }
    background_arg = "background"
    executable = "opencode"
    env = {"OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS": "true"}
    skills_dir = ".opencode/skills"
    install_entries = [
        ("agents", ".opencode/agents", InstallMode.RENDER_JINJA),
        ("skills", skills_dir, InstallMode.RENDER_JINJA),
        ("opencode/opencode.json", "opencode.json", InstallMode.MERGE_JSON),
        ("opencode/plugins/evotrias-bridge.ts", ".opencode/plugins/evotrias-bridge.ts", InstallMode.OVERWRITE),
    ]

    def mcp(self, tool: str) -> str:
        return f"{MCP_SERVER_NAME}_{tool}"

    def agent_frontmatter_fields(self, **fields: str) -> dict[str, str]:
        return {
            "description": fields["description"],
            "mode": "subagent",
        }

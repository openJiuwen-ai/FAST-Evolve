from __future__ import annotations

from evotrias.enums_and_consts import MCP_SERVER_NAME
from evotrias.harness.base import Harness


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

    def mcp(self, tool: str) -> str:
        return f"{MCP_SERVER_NAME}_{tool}"

    def agent_frontmatter_fields(self, **fields: str) -> dict[str, str]:
        return {
            "description": fields["description"],
            "mode": "subagent",
        }

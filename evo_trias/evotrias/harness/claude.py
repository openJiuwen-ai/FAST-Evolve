from __future__ import annotations

from evotrias.harness.base import MCP_SERVER, Harness


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

    def mcp(self, tool: str) -> str:
        return f"mcp__{MCP_SERVER}__{tool}"

    def agent_frontmatter_fields(self, **fields: str) -> dict[str, str]:
        return {
            "name": fields["name"],
            "description": fields["description"],
        }

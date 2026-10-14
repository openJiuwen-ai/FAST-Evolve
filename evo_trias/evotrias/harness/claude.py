from __future__ import annotations

from evotrias.enums_and_consts import MCP_SERVER_NAME
from evotrias.harness.base import Harness


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

    def mcp(self, tool: str) -> str:
        return f"mcp__{MCP_SERVER_NAME}__{tool}"

    def agent_frontmatter_fields(self, **fields: str) -> dict[str, str]:
        return {
            "name": fields["name"],
            "description": fields["description"],
        }

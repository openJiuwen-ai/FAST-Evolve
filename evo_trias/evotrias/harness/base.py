"""Harness abstraction: what differs between coding-agent hosts (Claude Code, opencode, ...)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Harness(ABC):
    name: str
    # tool name used in templates (``tool.read``) -> this harness's tool name
    tool_names: dict[str, str]
    # argument name of the subagent tool that runs a dispatch in the background
    background_arg: str

    @abstractmethod
    def mcp(self, tool: str) -> str:
        """Full name of an evotrias MCP tool as this harness exposes it."""

    @abstractmethod
    def agent_frontmatter_fields(self, **fields: str) -> dict[str, str]:
        """Pick the frontmatter fields this harness writes from what the template passed.

        Take each value by key (``fields["name"]``): a missing one fails install, and fields
        not picked are simply not written.  Anything not returned uses the harness default.
        """

    def agent_frontmatter(self, **fields: str) -> str:
        """Agent frontmatter body, without the ``---`` fences."""
        return "\n".join(f"{key}: {value}" for key, value in self.agent_frontmatter_fields(**fields).items())

    def template_context(self) -> dict[str, Any]:
        """Variables and functions available to every ``.jinja`` template."""
        return {
            "harness": self.name,
            "mcp": self.mcp,
            "tool": dict(self.tool_names),
            "background_arg": self.background_arg,
            "agent_frontmatter": self.agent_frontmatter,
        }

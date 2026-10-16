"""Harness abstraction: what differs between coding-agent hosts (Claude Code, opencode, ...)."""

from __future__ import annotations

import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from evotrias.enums_and_consts import InstallMode

_DETECT_TIMEOUT_S = 10


@dataclass(frozen=True)
class LaunchCommand:
    argv: list[str]
    # variables the harness needs; they override the caller's environment
    env: dict[str, str]


class Harness(ABC):
    name: str
    # tool name used in templates (``tool.read``) -> this harness's tool name
    tool_names: dict[str, str]
    # argument name of the subagent tool that runs a dispatch in the background
    background_arg: str
    # command name looked up on PATH
    executable: str
    # option -> (default value, user options that make it step aside)
    default_args: dict[str, tuple[str, set[str]]] = {}
    # environment variables injected at launch
    env: dict[str, str] = {}
    # workspace directory the shared skills go to; users drop their problem skills here too
    skills_dir: str
    # (source under templates/, destination in the workspace, how it is installed)
    install_entries: list[tuple[str, str, InstallMode]]

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

    def launch_command(self, user_args: list[str]) -> LaunchCommand:
        """Defaults first, then the user's arguments verbatim.  A default is dropped when the
        user passes any option it yields to, as ``--x value`` or ``--x=value``."""
        given = {arg.split("=", 1)[0] for arg in user_args if arg.startswith("-")}
        argv = [self.executable]
        for option, (value, yields_to) in self.default_args.items():
            if not given & yields_to:
                argv += [option, value]
        return LaunchCommand(argv + list(user_args), dict(self.env))

    def detect(self) -> str | None:
        """First non-empty line of ``<executable> --version``; None when missing or not runnable."""
        # run the resolved path: on Windows a bare name only finds .exe, not npm's .cmd shims
        path = shutil.which(self.executable)
        if path is None:
            return None
        try:
            done = subprocess.run(
                [path, "--version"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_DETECT_TIMEOUT_S,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if done.returncode != 0:
            return None
        return next((line.strip() for line in done.stdout.splitlines() if line.strip()), None)

from __future__ import annotations

from enum import Enum


class Agents(Enum):
    MAIN = "main"  # agent_info: {"agent_type"}
    INIT_EXPLORER = "init_explorer"  # agent_info: {"agent_type"}
    META_GUIDANCE_GENERATOR = "meta_guidance_generator"  # agent_info: {"agent_type"}
    TASK_EXECUTOR = "task_executor"  # agent_info: {"agent_type", "task_id"}
    UNKNOWN = "unknown"  # agent_info: {"agent_type", "origin_agent_type"}


class Tools(Enum):
    READ = "read"  # tool_param: {"file_path": str}
    WRITE = "write"  # tool_param: {"file_path": str, "content": str}
    EDIT = "edit"  # tool_param: {"file_path": str, "old_string": str, "new_string": str}
    BASH = "bash"  # tool_param: {"command": str}
    GLOB = "glob"  # tool_param: {"pattern": str, "path": str|None}
    GREP = "grep"  # tool_param: {"pattern": str, "path": str|None, "glob": str|None}
    UNKNOWN = "unknown"  # tool_param: {"raw_tool_name": str, "raw_tool_input": dict}


class Decisions(Enum):
    ALLOW = "allow"  # endorses the call, bypasses the native permission prompt
    DENY = "deny"  # forbids the call; reason surfaced to Claude
    BYPASS = "bypass"  # no opinion -> native permission flow runs


class SearchState(Enum):
    """Search phase of one run, persisted in run_state.json."""

    EXPLORING = "exploring"
    WATCHING = "watching"
    STOPPED = "stopped"

from __future__ import annotations

from enum import Enum


class Agents(Enum):
    MAIN = "main"                                        # agent_info: {"agent_type"}
    INIT_EXPLORER = "init_explorer"                      # agent_info: {"agent_type"}
    META_GUIDANCE_GENERATOR = "meta_guidance_generator"  # agent_info: {"agent_type"}
    TASK_EXECUTOR = "task_executor"                      # agent_info: {"agent_type", "task_id"}
    UNKNOWN = "unknown"                                  # agent_info: {"agent_type", "origin_agent_type"}

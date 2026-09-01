from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from evotrias.enums import Decisions, Tools

DEFAULT_ALLOW_REASON = "EvoTrias automatically allows tool execution based on predefined rules."


class Handler(ABC):
    rule_name: str = "base"
    priority: int = 0

    @staticmethod
    @abstractmethod
    def filter(
        tool_type: Tools, tool_param: dict[str, Any], run_id: str, agent_info: dict[str, Any]
    ) -> bool:
        raise NotImplementedError

    @staticmethod
    @abstractmethod
    def check(
        tool_type: Tools, tool_param: dict[str, Any], run_id: str, agent_info: dict[str, Any]
    ) -> tuple[Decisions, str]:
        raise NotImplementedError

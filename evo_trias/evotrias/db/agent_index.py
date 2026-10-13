"""Which agent is doing what in a run, persisted as ``agent_index.json``.

Keyed by the host's agent/session id.  ``attrs`` carries the fields that
depend on ``agent_type`` (see ``evotrias.enums_and_consts.Agents``): a task executor
records its ``task_id``, an unrecognised agent records ``origin_agent_type``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from evotrias.db import paths
from evotrias.db.base import DBObject
from evotrias.enums_and_consts import Agents


@dataclass
class AgentInfo:
    agent_type: Agents
    attrs: Dict[str, Any] = field(default_factory=dict)


class AgentIndex(DBObject):
    agents: Dict[str, AgentInfo] = field(default_factory=dict)

    def __init__(self, run_id: str):
        super().__init__(paths.run_dir(run_id) / paths.AGENT_INDEX)

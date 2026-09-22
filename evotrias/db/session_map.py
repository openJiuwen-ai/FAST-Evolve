"""Host session id -> run id, persisted globally as ``session_map.json``.

Written when ``configure_run`` is observed for a session; hooks use it to
find the run an event belongs to.
"""

from __future__ import annotations

from dataclasses import field
from typing import Dict

from evotrias.db import paths
from evotrias.db.base import DBObject


class SessionMap(DBObject):
    sessions: Dict[str, str] = field(default_factory=dict)

    def __init__(self):
        super().__init__(paths.SESSION_MAP)

# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Complete timeline of the meta-guidance loop, persisted as ``meta_guidance_events.jsonl``.

Deliberately overlaps with ``meta_guidance_state.json``: one ordered stream
covers the whole lifecycle (watching started, request created, guidance
committed, assigned, recorded, archived, watching reset) so the dashboard
and the run report read a single file.  ``payload`` holds the fields that
differ per ``event_type``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evotrias.db import paths
from evotrias.db.base import DBList
from evotrias.utils.time import utc_now_iso


@dataclass
class MetaGuidanceEvent:
    event_type: str
    run_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)


class MetaGuidanceEvents(DBList):
    def __init__(self, run_id: str):
        super().__init__(paths.run_dir(run_id) / paths.META_GUIDANCE_EVENTS, MetaGuidanceEvent)

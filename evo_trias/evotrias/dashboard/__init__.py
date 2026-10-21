# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Read-only run dashboard: a small HTTP server plus the static UI in ``static/``.

It only reads run state under ``.evotrias/db`` and never writes anything;
dependency direction is ``dashboard -> db``.  Started (optionally) by the
MCP ``configure_run`` tool; observability is never run-critical.
"""

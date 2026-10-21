# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""MCP entry point: the FastMCP server object plus tool registration.

This package is a thin shell.  ``public_tools`` declares the agent-facing
contract (tool names, arguments, defaults, docstrings) and forwards every call
to ``evotrias.evolve_ctrl.controller``; nothing here touches db objects.
Dependency direction is ``mcp -> evolve_ctrl -> db``; the read-only dashboard
is started from ``configure_run`` as a side effect.  The contract is documented
in ``docs/mcp_interface.md``.
"""

from evotrias.mcp.mcp_server import mcp

# Importing public_tools registers the FastMCP tools on the server instance.
from . import public_tools as public_tools

__all__ = ["mcp", "public_tools"]

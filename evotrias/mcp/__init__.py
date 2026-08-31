"""Agent Evolve MCP package exports."""

from evotrias.mcp.mcp_server import mcp

# Importing public_tools registers the FastMCP tools on the server instance.
from . import public_tools as public_tools

__all__ = ["mcp", "public_tools"]

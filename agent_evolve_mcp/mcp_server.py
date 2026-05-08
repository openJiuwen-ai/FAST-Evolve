"""FastMCP server object for the Agent Evolve controller."""

from fastmcp import FastMCP

mcp = FastMCP("agent_evolve")

# Register public tools when the server module is imported directly.
import agent_evolve_mcp.public_tools  # noqa: E402,F401

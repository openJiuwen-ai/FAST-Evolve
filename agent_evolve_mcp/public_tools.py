from fastmcp import Context

from agent_evolve_mcp.mcp_server import mcp

@mcp.tool()
def foo(ctx: Context):
    """Foo Tool"""
    return "Foo!"

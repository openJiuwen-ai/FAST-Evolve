"""FastMCP server object for the EvoTrias controller."""

from fastmcp import FastMCP

from evotrias.enums_and_consts import MCP_SERVER_NAME

mcp = FastMCP(MCP_SERVER_NAME)

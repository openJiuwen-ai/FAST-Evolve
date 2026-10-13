from evotrias.enums_and_consts import MCP_SERVER_NAME, Agents, Decisions, Tools
from evotrias.mcp.mcp_server import mcp


def test_tools_members():
    assert {t.name for t in Tools} == {"READ", "WRITE", "EDIT", "BASH", "GLOB", "GREP", "AGENT", "UNKNOWN"}


def test_agents_members():
    assert {a.name for a in Agents} == {
        "MAIN",
        "INIT_EXPLORER",
        "META_GUIDANCE_GENERATOR",
        "TASK_EXECUTOR",
        "UNKNOWN",
    }


def test_decisions_members():
    assert {d.name for d in Decisions} == {"ALLOW", "DENY", "BYPASS"}


def test_mcp_server_name_matches_constant():
    # harness tool names (mcp__evotrias__<tool>, evotrias_<tool>) are derived from the constant
    assert MCP_SERVER_NAME == "evotrias"
    assert mcp.name == MCP_SERVER_NAME

from evotrias.enums import Agents, Decisions, Tools


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

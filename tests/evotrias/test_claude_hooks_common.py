"""Tests for evotrias.hooks.claude.common."""

from __future__ import annotations

import pytest

from evotrias.enums import Agents
from evotrias.hooks.claude.common import current_tool_input, merge_pre_tool_use_output, resolve_agent_name


def test_current_tool_input_falls_back_to_original():
    payload = {"tool_input": {"prompt": "p"}}

    assert current_tool_input(payload, {}) == {"prompt": "p"}
    assert current_tool_input({}, {}) == {}


def test_current_tool_input_prefers_earlier_handlers_update():
    payload = {"tool_input": {"prompt": "p", "isolation": "worktree"}}
    result = {"hookSpecificOutput": {"updatedInput": {"prompt": "p"}}}

    assert current_tool_input(payload, result) == {"prompt": "p"}


def test_merge_keeps_existing_fields_and_adds_new_ones():
    result = {"other": 1, "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"}}

    out = merge_pre_tool_use_output(result, updatedInput={"prompt": "p"})

    assert out == {
        "other": 1,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": {"prompt": "p"},
        },
    }
    assert "updatedInput" not in result["hookSpecificOutput"], "the input result must not be mutated"


def test_merge_starts_from_empty_result():
    out = merge_pre_tool_use_output({}, updatedInput={"prompt": "p"})

    assert out == {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {"prompt": "p"}}}


def test_merge_never_downgrades_deny_to_allow():
    denied = merge_pre_tool_use_output({}, permissionDecision="deny", permissionDecisionReason="no")

    out = merge_pre_tool_use_output(denied, permissionDecision="allow", permissionDecisionReason="yes")

    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert out["hookSpecificOutput"]["permissionDecisionReason"] == "no"


def test_merge_lets_deny_override_allow():
    allowed = merge_pre_tool_use_output({}, permissionDecision="allow", permissionDecisionReason="yes")

    out = merge_pre_tool_use_output(allowed, permissionDecision="deny", permissionDecisionReason="no")

    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert out["hookSpecificOutput"]["permissionDecisionReason"] == "no"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("evotrias_plugin:task-executor", Agents.TASK_EXECUTOR),
        ("evotrias_plugin:init-explorer", Agents.INIT_EXPLORER),
        ("evotrias_plugin:meta-guidance-generator", Agents.META_GUIDANCE_GENERATOR),
        ("  EvoTrias_Plugin:Task-Executor ", Agents.TASK_EXECUTOR),
        ("Explore", Agents.UNKNOWN),
        ("", Agents.UNKNOWN),
        (None, Agents.UNKNOWN),
    ],
)
def test_resolve_agent_name(raw, expected):
    assert resolve_agent_name(raw) == expected

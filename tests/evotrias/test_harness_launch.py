"""Tests for the harness launcher description: launch_command, detect and the registry."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from evotrias.enums_and_consts import InstallMode
from evotrias.setup.harness import base
from evotrias.setup.harness.claude import ClaudeHarness
from evotrias.setup.harness.opencode import OpencodeHarness
from evotrias.setup.harness.registry import HARNESSES, get_harness

# ---- launch_command ----


def test_claude_adds_default_permission_mode():
    command = ClaudeHarness().launch_command([])

    assert command.argv == ["claude", "--permission-mode", "auto"]
    assert command.env == {}


def test_user_args_follow_defaults_verbatim():
    command = ClaudeHarness().launch_command(["--resume", "--model", "opus"])

    assert command.argv == ["claude", "--permission-mode", "auto", "--resume", "--model", "opus"]


@pytest.mark.parametrize(
    "user_args",
    [
        ["--permission-mode", "plan"],
        ["--permission-mode=plan"],
        ["--dangerously-skip-permissions"],
    ],
    ids=["space", "equals", "skip-permissions"],
)
def test_user_option_replaces_default(user_args):
    command = ClaudeHarness().launch_command(user_args)

    assert command.argv == ["claude", *user_args]


def test_opencode_has_no_default_args_and_injects_env():
    command = OpencodeHarness().launch_command(["--continue"])

    assert command.argv == ["opencode", "--continue"]
    assert command.env == {"OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS": "true"}


# ---- detect ----


@pytest.fixture
def fake_run(monkeypatch):
    """Resolve the executable to a fake path and record calls to subprocess.run."""
    calls = []
    outcome = {"result": SimpleNamespace(returncode=0, stdout="")}

    def run(argv, **kwargs):
        calls.append(argv)
        if isinstance(outcome["result"], BaseException):
            raise outcome["result"]
        return outcome["result"]

    monkeypatch.setattr(base.shutil, "which", lambda name: f"/fake/bin/{name}.cmd")
    monkeypatch.setattr(base.subprocess, "run", run)
    return calls, outcome


def test_detect_returns_none_when_not_on_path(monkeypatch):
    monkeypatch.setattr(base.shutil, "which", lambda name: None)

    assert ClaudeHarness().detect() is None


def test_detect_runs_the_resolved_path(fake_run):
    calls, outcome = fake_run
    outcome["result"] = SimpleNamespace(returncode=0, stdout="1.18.23\n")

    assert OpencodeHarness().detect() == "1.18.23"
    assert calls == [["/fake/bin/opencode.cmd", "--version"]]


def test_detect_returns_first_non_empty_line(fake_run):
    _, outcome = fake_run
    outcome["result"] = SimpleNamespace(returncode=0, stdout="\n  2.1.226 (Claude Code)  \nextra\n")

    assert ClaudeHarness().detect() == "2.1.226 (Claude Code)"


@pytest.mark.parametrize(
    "result",
    [
        SimpleNamespace(returncode=1, stdout="2.1.226\n"),
        SimpleNamespace(returncode=0, stdout=""),
        SimpleNamespace(returncode=0, stdout="\n  \n"),
        subprocess.TimeoutExpired(cmd="claude", timeout=10),
        OSError("cannot execute"),
    ],
    ids=["non-zero-exit", "empty", "blank-lines", "timeout", "os-error"],
)
def test_detect_returns_none_when_version_fails(fake_run, result):
    _, outcome = fake_run
    outcome["result"] = result

    assert ClaudeHarness().detect() is None


# ---- registry ----


def test_registry_lists_harnesses_in_setup_order():
    assert [harness.name for harness in HARNESSES] == ["claude", "opencode"]


def test_get_harness_by_name():
    assert isinstance(get_harness("claude"), ClaudeHarness)
    assert isinstance(get_harness("opencode"), OpencodeHarness)


def test_get_unknown_harness_lists_known_names():
    with pytest.raises(ValueError, match=r"unknown harness 'claud'; expected one of \['claude', 'opencode'\]"):
        get_harness("claud")


def test_skills_dir_is_where_shared_skills_install():
    assert ClaudeHarness().skills_dir == ".claude/skills"
    assert OpencodeHarness().skills_dir == ".opencode/skills"
    for harness in HARNESSES:
        assert ("skills", harness.skills_dir, InstallMode.RENDER_JINJA) in harness.install_entries

# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Tests for evotrias.cli: argument routing, ``evotrias setup <harness>`` and the launcher."""

from __future__ import annotations

import shlex
import signal
import subprocess
import sys
from importlib.metadata import version
from types import SimpleNamespace

import pytest

from evotrias import cli
from evotrias.setup import setup_tui
from evotrias.setup.harness.claude import ClaudeHarness
from evotrias.setup.harness.opencode import OpencodeHarness
from evotrias.setup.workspace import ACTIVE_HARNESS


@pytest.fixture(autouse=True)
def workspace(log, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def launched(monkeypatch):
    """Record what the launcher would run instead of running it."""
    calls = []

    def fake_launch(args):
        calls.append(args)
        return 0

    monkeypatch.setattr(cli, "_launch", fake_launch)
    return calls


def _bind(workspace, name: str) -> None:
    (workspace / ACTIVE_HARNESS).parent.mkdir(parents=True, exist_ok=True)
    (workspace / ACTIVE_HARNESS).write_text(f"{name}\n", encoding="utf-8")


# ---- routing ----


@pytest.mark.parametrize(
    "args",
    [[], ["--resume"], ["--model", "opus"], ["-p", "fix the bug"], ["--ver"]],
    ids=["no-args", "flag", "option-value", "short-option", "not-an-abbreviation"],
)
def test_harness_arguments_go_to_the_launcher_verbatim(launched, args):
    assert cli.main(args) == 0
    assert launched == [args]


def test_version_is_evotrias_own(launched, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"evotrias {version('evotrias')}"
    assert launched == []


def test_help_is_evotrias_own(launched, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])

    assert exit_info.value.code == 0
    assert "setup" in capsys.readouterr().out
    assert launched == []


@pytest.mark.parametrize("args", [["foo"], ["setup", "claud"]], ids=["command", "harness"])
def test_bad_command_line_exits_2(launched, args):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(args)

    assert exit_info.value.code == 2
    assert launched == []


# ---- setup ----


def test_setup_without_a_harness_runs_the_tui(monkeypatch, workspace, launched):
    calls = []
    monkeypatch.setattr(setup_tui, "setup_tui", lambda path: calls.append(path) or 5)

    assert cli.main(["setup"]) == 5
    assert calls == [workspace]
    assert launched == []


def test_cli_loads_neither_jinja2_nor_textual():
    # the launcher must not pay for setup's imports: jinja2 (install) and textual (TUI)
    code = "import sys, evotrias.cli; print(sorted(m for m in ('jinja2', 'textual') if m in sys.modules))"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)

    assert result.stdout.strip() == "[]"


def test_setup_installs_and_reports_the_version(monkeypatch, workspace, capsys):
    monkeypatch.setattr(ClaudeHarness, "detect", lambda self: "2.1.226 (Claude Code)")

    assert cli.main(["setup", "claude"]) == 0

    out = capsys.readouterr()
    assert out.out == 'Set up for claude (2.1.226 (Claude Code)).\nRun "evotrias" to start.\n'
    assert out.err == ""
    assert (workspace / ACTIVE_HARNESS).read_text(encoding="utf-8") == "claude\n"
    assert (workspace / ".claude/settings.json").exists()


def test_setup_refuses_an_undetected_harness(monkeypatch, workspace, capsys):
    monkeypatch.setattr(OpencodeHarness, "detect", lambda self: None)

    assert cli.main(["setup", "opencode"]) == 1

    assert capsys.readouterr().err == (
        '"opencode" not found or not runnable. Install it, then re-run "evotrias setup opencode".\n'
    )
    assert list(workspace.iterdir()) == []


def test_setup_over_an_existing_workspace_warns_and_continues(monkeypatch, workspace, capsys):
    monkeypatch.setattr(OpencodeHarness, "detect", lambda self: "1.18.23")
    _bind(workspace, "claude")

    assert cli.main(["setup", "opencode"]) == 0

    assert capsys.readouterr().err == (
        "warning: this directory is already set up for claude; setting it up for opencode.\n"
    )
    assert (workspace / ACTIVE_HARNESS).read_text(encoding="utf-8") == "opencode\n"


# ---- launcher ----


@pytest.fixture(params=["linux", "win32"])
def platform(request, monkeypatch):
    """Run the launcher as on POSIX (exec) or Windows (waited subprocess), recording instead of running."""
    calls = []

    def fake_execve(path, argv, env):
        calls.append(SimpleNamespace(path=path, argv=argv, env=env, sigint=None))

    def fake_run(argv, env):
        calls.append(SimpleNamespace(path=argv[0], argv=argv, env=env, sigint=signal.getsignal(signal.SIGINT)))
        return subprocess.CompletedProcess(argv, 7)

    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setattr(cli.sys, "platform", request.param)  # restored after the test
    monkeypatch.setattr(cli.os, "execve", fake_execve)
    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    return SimpleNamespace(name=request.param, calls=calls)


def test_launch_without_workspace(capsys):
    assert cli.main([]) == 1
    assert capsys.readouterr().err == 'No EvoTrias workspace here. Run "evotrias setup" first.\n'


def test_launch_with_unknown_bound_harness(workspace, capsys):
    _bind(workspace, "vim")

    assert cli.main([]) == 1
    assert capsys.readouterr().err == (
        ".evotrias/active_harness names unknown harness 'vim'. Re-run \"evotrias setup\".\n"
    )


def test_launch_without_the_executable(monkeypatch, workspace, capsys):
    _bind(workspace, "claude")
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)

    assert cli.main([]) == 1
    assert capsys.readouterr().err == '"claude" not found on PATH.\n'


def test_launch_prints_and_runs_the_harness_command(platform, workspace, capsys):
    _bind(workspace, "claude")
    before = signal.getsignal(signal.SIGINT)

    code = cli.main(["--resume"])

    assert capsys.readouterr().out == (
        'Detected workspace bound to claude. Run "claude --permission-mode auto --resume".\n'
    )
    [call] = platform.calls
    if platform.name == "linux":
        # the process is replaced: exec gets the resolved path and the full argv
        assert (call.path, call.argv) == ("/fake/bin/claude", ["claude", "--permission-mode", "auto", "--resume"])
    else:
        # waited subprocess: its exit code is passed on, Ctrl-C is left to the harness meanwhile
        assert code == 7
        assert call.argv == ["/fake/bin/claude", "--permission-mode", "auto", "--resume"]
        assert call.sigint == signal.SIG_IGN
    assert signal.getsignal(signal.SIGINT) is before


def test_launch_shows_the_command_in_the_platform_shell_syntax(platform, workspace, capsys):
    _bind(workspace, "claude")

    cli.main(["-p", "fix the bug"])

    argv = ["claude", "--permission-mode", "auto", "-p", "fix the bug"]
    shown = shlex.join(argv) if platform.name == "linux" else subprocess.list2cmdline(argv)
    assert shown != " ".join(argv)  # the quoting differs per shell, which is the point
    assert capsys.readouterr().out == f'Detected workspace bound to claude. Run "{shown}".\n'


def test_launch_env_lets_the_harness_override_the_user(monkeypatch, platform, workspace):
    _bind(workspace, "opencode")
    monkeypatch.setenv("OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS", "false")
    monkeypatch.setenv("EVOTRIAS_TEST_USER_VAR", "kept")

    cli.main([])

    [call] = platform.calls
    assert call.env["OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS"] == "true"
    assert call.env["EVOTRIAS_TEST_USER_VAR"] == "kept"

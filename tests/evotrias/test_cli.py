"""Tests for evotrias.cli: argument routing, ``evotrias setup <harness>`` and the launcher."""

from __future__ import annotations

import signal
import subprocess
from importlib.metadata import version
from types import SimpleNamespace

import pytest

from evotrias import cli
from evotrias.db import paths
from evotrias.harness.claude import ClaudeHarness
from evotrias.harness.opencode import OpencodeHarness


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
    (workspace / paths.ACTIVE_HARNESS).parent.mkdir(parents=True, exist_ok=True)
    (workspace / paths.ACTIVE_HARNESS).write_text(f"{name}\n", encoding="utf-8")


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


@pytest.mark.parametrize("args", [["foo"], ["setup"], ["setup", "claud"]], ids=["command", "missing", "harness"])
def test_bad_command_line_exits_2(launched, args):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(args)

    assert exit_info.value.code == 2
    assert launched == []


# ---- setup ----


def test_setup_installs_and_reports_the_version(monkeypatch, workspace, capsys):
    monkeypatch.setattr(ClaudeHarness, "detect", lambda self: "2.1.226 (Claude Code)")

    assert cli.main(["setup", "claude"]) == 0

    out = capsys.readouterr()
    assert out.out == 'Set up for claude (2.1.226 (Claude Code)).\nRun "evotrias" to start.\n'
    assert out.err == ""
    assert (workspace / paths.ACTIVE_HARNESS).read_text(encoding="utf-8") == "claude\n"
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
    assert (workspace / paths.ACTIVE_HARNESS).read_text(encoding="utf-8") == "opencode\n"


# ---- launcher ----


@pytest.fixture
def run_recorder(monkeypatch):
    """Resolve executables to a fake path and capture the platform runner's arguments."""
    calls = []

    def fake_run(path, argv, env):
        calls.append(SimpleNamespace(path=path, argv=argv, env=env))
        return 7

    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setattr(cli, "_run_windows", fake_run)
    monkeypatch.setattr(cli, "_exec_posix", fake_run)
    return calls


def test_launch_without_workspace(capsys):
    assert cli.main([]) == 1
    assert capsys.readouterr().err == 'No EvoTrias workspace here. Run "evotrias setup <harness>" first.\n'


def test_launch_with_unknown_bound_harness(workspace, capsys):
    _bind(workspace, "vim")

    assert cli.main([]) == 1
    assert capsys.readouterr().err == (
        ".evotrias/active_harness names unknown harness 'vim'. Re-run \"evotrias setup <harness>\".\n"
    )


def test_launch_without_the_executable(monkeypatch, workspace, capsys):
    _bind(workspace, "claude")
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)

    assert cli.main([]) == 1
    assert capsys.readouterr().err == '"claude" not found on PATH.\n'


def test_launch_prints_and_runs_the_harness_command(run_recorder, workspace, capsys):
    _bind(workspace, "claude")

    assert cli.main(["--resume"]) == 7

    assert capsys.readouterr().out == (
        'Detected workspace bound to claude. Run "claude --permission-mode auto --resume".\n'
    )
    [call] = run_recorder
    assert call.path == "/fake/bin/claude"
    assert call.argv == ["claude", "--permission-mode", "auto", "--resume"]


def test_launch_env_lets_the_harness_override_the_user(monkeypatch, run_recorder, workspace):
    _bind(workspace, "opencode")
    monkeypatch.setenv("OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS", "false")
    monkeypatch.setenv("EVOTRIAS_TEST_USER_VAR", "kept")

    cli.main([])

    [call] = run_recorder
    assert call.env["OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS"] == "true"
    assert call.env["EVOTRIAS_TEST_USER_VAR"] == "kept"


def test_exec_posix_replaces_the_process(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.os, "execve", lambda path, argv, env: calls.append((path, argv, env)))

    cli._exec_posix("/usr/bin/claude", ["claude", "--resume"], {"K": "V"})

    assert calls == [("/usr/bin/claude", ["claude", "--resume"], {"K": "V"})]


def test_run_windows_returns_exit_code_and_restores_sigint(monkeypatch):
    calls = []

    def fake_run(argv, env):
        calls.append((argv, env, signal.getsignal(signal.SIGINT)))
        return subprocess.CompletedProcess(argv, 3)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    before = signal.getsignal(signal.SIGINT)

    assert cli._run_windows(r"C:\npm\opencode.CMD", ["opencode", "--continue"], {"K": "V"}) == 3

    assert calls == [([r"C:\npm\opencode.CMD", "--continue"], {"K": "V"}, signal.SIG_IGN)]
    assert signal.getsignal(signal.SIGINT) is before

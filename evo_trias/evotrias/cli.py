"""``evotrias`` command: ``evotrias setup <harness>`` and the launcher ``evotrias [harness options...]``."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import signal
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

from evotrias.setup.harness.registry import HARNESSES, get_harness
from evotrias.setup.workspace import ACTIVE_HARNESS, read_active_harness

# the only leading options evotrias keeps for itself; any other leading option goes to the harness
_OWN_OPTIONS = ("-h", "--help", "--version")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    # argparse cannot tell which harness options take a value (`--model opus` would make `opus`
    # a subcommand), so harness arguments never reach it
    if not args or (args[0].startswith("-") and args[0] not in _OWN_OPTIONS):
        return _launch(args)
    ns = _parser().parse_args(args)
    # setup modules load lazily: the launcher must not pay for jinja2 or textual
    if ns.harness is None:
        from evotrias.setup.setup_tui import setup_tui

        return setup_tui(Path.cwd())
    from evotrias.setup.setup_cmd import setup_cmd

    return setup_cmd(ns.harness, Path.cwd())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evotrias",
        description="Set up an EvoTrias workspace, or launch its harness: evotrias [harness options...]",
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"evotrias {version('evotrias')}")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser(
        "setup", help="set the current directory up as a workspace (interactive without a harness)"
    )
    setup.add_argument("harness", nargs="?", choices=[harness.name for harness in HARNESSES])
    return parser


def _launch(user_args: list[str]) -> int:
    name = read_active_harness(Path.cwd())
    if name is None:
        print('No EvoTrias workspace here. Run "evotrias setup" first.', file=sys.stderr)
        return 1
    try:
        harness = get_harness(name)
    except ValueError:
        print(
            f'{ACTIVE_HARNESS.as_posix()} names unknown harness {name!r}. Re-run "evotrias setup".',
            file=sys.stderr,
        )
        return 1
    command = harness.launch_command(user_args)
    # resolve first: on Windows a bare name only finds .exe, not npm's .cmd shims
    path = shutil.which(harness.executable)
    if path is None:
        print(f'"{harness.executable}" not found on PATH.', file=sys.stderr)
        return 1
    windows = sys.platform == "win32"
    shown = subprocess.list2cmdline(command.argv) if windows else shlex.join(command.argv)
    print(f'Detected workspace bound to {harness.name}. Run "{shown}".', flush=True)
    env = {**os.environ, **command.env}
    if not windows:
        os.execve(path, command.argv, env)  # replaces this process
        return 0  # unreachable unless os.execve is replaced
    # Windows has no real exec (os.execv spawns and exits, leaving the shell and the harness
    # fighting over the console), so wait for the harness and pass its exit code on.  Ctrl-C
    # reaches every process on the console; let the harness handle it alone.
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        return subprocess.run([path, *command.argv[1:]], env=env).returncode
    finally:
        signal.signal(signal.SIGINT, previous)

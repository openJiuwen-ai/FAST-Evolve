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

from evotrias.db import paths
from evotrias.harness.registry import HARNESSES, get_harness
from evotrias.setup.install import install

# the only leading options evotrias keeps for itself; any other leading option goes to the harness
_OWN_OPTIONS = ("-h", "--help", "--version")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    # argparse cannot tell which harness options take a value (`--model opus` would make `opus`
    # a subcommand), so harness arguments never reach it
    if not args or (args[0].startswith("-") and args[0] not in _OWN_OPTIONS):
        return _launch(args)
    ns = _parser().parse_args(args)
    return _setup(ns.harness)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evotrias",
        description="Set up an EvoTrias workspace, or launch its harness: evotrias [harness options...]",
        allow_abbrev=False,
    )
    parser.add_argument("--version", action="version", version=f"evotrias {version('evotrias')}")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup", help="set the current directory up as a workspace")
    setup.add_argument("harness", choices=[harness.name for harness in HARNESSES])
    return parser


def _setup(name: str) -> int:
    harness = get_harness(name)
    found = harness.detect()
    if found is None:
        print(
            f'"{harness.executable}" not found or not runnable. '
            f'Install it, then re-run "evotrias setup {harness.name}".',
            file=sys.stderr,
        )
        return 1
    if paths.ACTIVE_HARNESS.exists():
        previous = paths.ACTIVE_HARNESS.read_text(encoding="utf-8").strip()
        print(
            f"warning: this directory is already set up for {previous}; setting it up for {harness.name}.",
            file=sys.stderr,
        )
    install(harness, Path.cwd())
    print(f"Set up for {harness.name} ({found}).")
    print('Run "evotrias" to start.')
    return 0


def _launch(user_args: list[str]) -> int:
    if not paths.ACTIVE_HARNESS.exists():
        print('No EvoTrias workspace here. Run "evotrias setup <harness>" first.', file=sys.stderr)
        return 1
    name = paths.ACTIVE_HARNESS.read_text(encoding="utf-8").strip()
    try:
        harness = get_harness(name)
    except ValueError:
        print(
            f'{paths.ACTIVE_HARNESS.as_posix()} names unknown harness {name!r}. Re-run "evotrias setup <harness>".',
            file=sys.stderr,
        )
        return 1
    command = harness.launch_command(user_args)
    # resolve first: on Windows a bare name only finds .exe, not npm's .cmd shims
    path = shutil.which(harness.executable)
    if path is None:
        print(f'"{harness.executable}" not found on PATH.', file=sys.stderr)
        return 1
    print(f'Detected workspace bound to {harness.name}. Run "{_display(command.argv)}".', flush=True)
    env = {**os.environ, **command.env}
    run = _run_windows if os.name == "nt" else _exec_posix
    return run(path, command.argv, env)


def _display(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def _exec_posix(path: str, argv: list[str], env: dict[str, str]) -> int:
    """Replace this process with the harness; only returns if exec itself fails (it raises then)."""
    os.execve(path, argv, env)
    return 0  # unreachable unless os.execve is replaced


def _run_windows(path: str, argv: list[str], env: dict[str, str]) -> int:
    """Windows has no real exec (os.execv spawns and exits, leaving the shell and the harness
    fighting over the console), so wait for the harness and pass its exit code on."""
    # Ctrl-C reaches every process on the console; let the harness handle it alone
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        return subprocess.run([path, *argv[1:]], env=env).returncode
    finally:
        signal.signal(signal.SIGINT, previous)

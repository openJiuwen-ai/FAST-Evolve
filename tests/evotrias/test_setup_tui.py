"""Tests for evotrias.setup.setup_tui, driven headless through textual's Pilot."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest
from textual.widgets import ContentSwitcher, OptionList, Static

from evotrias.enums_and_consts import InstallMode
from evotrias.setup import install, setup_tui
from evotrias.setup.setup_tui import SetupApp
from evotrias.setup.workspace import ACTIVE_HARNESS

FOUND = {"claude": "2.1.226 (Claude Code)", "opencode": "1.18.23"}


@pytest.fixture(autouse=True)
def _quiet(log, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(setup_tui, "STEP_INTERVAL_S", 0.01)


def _detector(found: dict[str, str], gate: threading.Event | None = None):
    def detect(harness):
        if gate is not None:
            gate.wait(5)
        return found.get(harness.name)

    return detect


def _run(app: SetupApp, scenario) -> None:
    async def main():
        async with app.run_test(size=(110, 30)) as pilot:
            await scenario(pilot)

    asyncio.run(main())


async def _until(pilot, condition, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not reached"
        await pilot.pause(0.02)


def _page(app: SetupApp) -> str | None:
    return app.query_one(ContentSwitcher).current


def _text(app: SetupApp, selector: str) -> str:
    return str(app.query_one(selector, Static).content)


def _rows(app: SetupApp) -> list[tuple[str, bool]]:
    harnesses = app.query_one("#harnesses", OptionList)
    rows = [harnesses.get_option_at_index(i) for i in range(harnesses.option_count)]
    return [(" ".join(str(row.prompt).split()), row.disabled) for row in rows]


def _bind(workspace: Path, name: str) -> None:
    (workspace / ACTIVE_HARNESS).parent.mkdir(parents=True, exist_ok=True)
    (workspace / ACTIVE_HARNESS).write_text(f"{name}\n", encoding="utf-8")


async def _press_button(pilot, app: SetupApp, button_id: str) -> None:
    """Press the focused button with Enter; a click can miss while the page is still laying out."""
    await _until(pilot, lambda: app.focused is not None and app.focused.id == button_id)
    await pilot.press("enter")


def _files(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


# ---- welcome ----


def test_welcome_in_a_plain_directory(tmp_path):
    app = SetupApp(tmp_path, _detector(FOUND))

    async def scenario(pilot):
        assert _page(app) == "welcome"
        assert "This will turn" in str(app.query_one("#welcome .body", Static).content)
        assert app.focused is not None and app.focused.id == "continue"

    _run(app, scenario)


def test_welcome_in_an_existing_workspace(tmp_path):
    _bind(tmp_path, "claude")
    app = SetupApp(tmp_path, _detector(FOUND))

    async def scenario(pilot):
        assert "already a claude workspace" in str(app.query_one("#welcome .warn", Static).content)

    _run(app, scenario)


# ---- choosing a harness ----


def test_rows_show_version_current_and_not_found(tmp_path):
    _bind(tmp_path, "opencode")
    app = SetupApp(tmp_path, _detector({"claude": FOUND["claude"]}))

    async def scenario(pilot):
        await _until(pilot, lambda: len(app.versions) == 2)
        await _press_button(pilot, app, "continue")
        assert _page(app) == "select"
        assert _rows(app) == [("claude 2.1.226 (Claude Code)", False), ("opencode current", True)]
        harnesses = app.query_one("#harnesses", OptionList)
        assert harnesses.highlighted == 0 and app.focused is harnesses

    _run(app, scenario)


def test_choice_list_fills_in_while_detecting(tmp_path):
    gate = threading.Event()
    app = SetupApp(tmp_path, _detector(FOUND, gate))

    async def scenario(pilot):
        await _press_button(pilot, app, "continue")
        assert _page(app) == "select"
        assert _rows(app) == [("claude detecting…", True), ("opencode detecting…", True)]

        gate.set()
        await _until(pilot, lambda: len(app.versions) == 2)
        assert _rows(app) == [("claude 2.1.226 (Claude Code)", False), ("opencode 1.18.23", False)]
        harnesses = app.query_one("#harnesses", OptionList)
        assert harnesses.highlighted == 0 and app.focused is harnesses

    _run(app, scenario)


def test_nothing_found_waits_for_every_detection(tmp_path):
    gate = threading.Event()
    app = SetupApp(tmp_path, _detector({}, gate))

    async def scenario(pilot):
        await _press_button(pilot, app, "continue")
        assert _page(app) == "select"  # still detecting: no verdict yet

        gate.set()
        await _until(pilot, lambda: _page(app) == "nothing")
        assert _text(app, "#nothing-body").startswith("No supported harness found.")
        await _press_button(pilot, app, "okay")

    _run(app, scenario)
    assert app.return_value == 0


def test_only_the_current_harness_found(tmp_path):
    _bind(tmp_path, "claude")
    app = SetupApp(tmp_path, _detector({"claude": FOUND["claude"]}))

    async def scenario(pilot):
        await _until(pilot, lambda: len(app.versions) == 2)
        await _press_button(pilot, app, "continue")
        assert _text(app, "#nothing-body").startswith(
            "This workspace is already set up for claude, the only harness found."
        )

    _run(app, scenario)


# ---- setting up ----


@pytest.mark.parametrize("name", ["claude", "opencode"])
def test_selecting_a_harness_sets_the_workspace_up(tmp_path, name):
    app = SetupApp(tmp_path, _detector(FOUND))

    async def scenario(pilot):
        await _until(pilot, lambda: len(app.versions) == 2)
        await _press_button(pilot, app, "continue")
        if name == "opencode":
            await pilot.press("down")
        await pilot.press("enter")
        await _until(pilot, lambda: _page(app) == "done")
        steps = _text(app, "#steps")
        assert steps.count("✓") == 5 and "…" not in steps
        assert ".evotrias/active_harness" in steps
        assert f".{name}/skills/" in _text(app, "#done-body")
        await _press_button(pilot, app, "exit")

    _run(app, scenario)
    assert app.return_value == 0
    assert (tmp_path / ACTIVE_HARNESS).read_text(encoding="utf-8") == f"{name}\n"
    assert any(rel.startswith(f".{name}/agents/") for rel in _files(tmp_path))


def test_quitting_before_setting_up_writes_nothing(tmp_path):
    app = SetupApp(tmp_path, _detector(FOUND))

    async def scenario(pilot):
        await _until(pilot, lambda: len(app.versions) == 2)
        await _press_button(pilot, app, "continue")
        await pilot.press("escape")

    _run(app, scenario)
    assert app.return_value == 0
    assert _files(tmp_path) == set()


def test_a_failing_step_is_shown_and_exits_1(monkeypatch, tmp_path):
    def broken(src, dst, context):
        raise PermissionError("read-only workspace")

    monkeypatch.setitem(install.STRATEGIES, InstallMode.MERGE_JSON, broken)
    app = SetupApp(tmp_path, _detector(FOUND))

    async def scenario(pilot):
        await _until(pilot, lambda: len(app.versions) == 2)
        await _press_button(pilot, app, "continue")
        await pilot.press("enter")
        await _until(pilot, lambda: "✗" in _text(app, "#steps"))
        assert "PermissionError: read-only workspace" in _text(app, "#steps")
        await _press_button(pilot, app, "failed")

    _run(app, scenario)
    assert app.return_value == 1

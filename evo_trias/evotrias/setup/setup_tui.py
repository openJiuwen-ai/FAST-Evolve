"""Interactive ``evotrias setup`` (design.md §6.1): welcome, choose a harness, set up, done.

Harnesses are detected in background threads as soon as the app starts, so the welcome page shows
at once; the choice list fills in as each detection finishes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from functools import partial
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.timer import Timer
from textual.widgets import Button, ContentSwitcher, OptionList, Static
from textual.widgets.option_list import Option

from evotrias.setup.harness.base import Harness
from evotrias.setup.harness.registry import HARNESSES, get_harness
from evotrias.setup.install import install
from evotrias.setup.tui_art import (
    AMBER,
    BG,
    CLAY,
    CYAN,
    DIM,
    FAINT,
    HIGHLIGHT,
    HOVER,
    INK,
    LEFT_HEIGHT,
    LEFT_WIDTH,
    LINE,
    PANEL,
    TEAL,
    render_dino,
)
from evotrias.setup.workspace import read_active_harness

# install itself takes milliseconds; pace the steps so each line can be seen
STEP_INTERVAL_S = 0.15


def _detect(harness: Harness) -> str | None:
    return harness.detect()


class SetupApp(App[int]):
    TITLE = "evotrias setup"
    CSS = f"""
    Screen {{ background: {BG}; color: {INK}; }}
    #root {{ height: 100%; padding: 1 2; }}
    #dino {{ width: {LEFT_WIDTH}; height: {LEFT_HEIGHT}; padding: 0 1 0 0; }}
    #right {{ width: 1fr; height: {LEFT_HEIGHT}; border: round {LINE}; background: {PANEL}; padding: 1 2; }}
    .title {{ color: {CYAN}; text-style: bold; margin-bottom: 1; }}
    .body {{ color: {INK}; margin-bottom: 1; }}
    .dim {{ color: {DIM}; }}
    .warn {{ color: {AMBER}; margin-bottom: 1; }}
    .err {{ color: {CLAY}; margin-bottom: 1; }}
    .ok {{ color: {TEAL}; text-style: bold; margin-bottom: 1; }}
    .buttons {{ height: auto; margin-top: 1; }}
    Button {{ margin-right: 2; min-width: 12; background: {PANEL}; color: {INK}; border: tall {FAINT}; }}
    Button:focus {{ color: {BG}; background: {CYAN}; border: tall {CYAN}; text-style: bold; }}
    Button:hover {{ border: tall {CYAN}; }}
    OptionList {{ height: auto; max-height: 8; background: {PANEL}; border: none; padding: 0; margin-bottom: 1; }}
    OptionList > .option-list--option-disabled {{ background: {PANEL}; }}
    OptionList > .option-list--option-hover {{ background: {HOVER}; }}
    OptionList > .option-list--option-highlighted {{ background: {HIGHLIGHT}; text-style: bold; }}
    OptionList:focus > .option-list--option-highlighted {{ background: {HIGHLIGHT}; text-style: bold; }}
    ContentSwitcher {{ height: auto; }}
    ContentSwitcher > Vertical {{ height: auto; }}
    #steps {{ height: auto; }}
    """
    BINDINGS = [Binding("escape", "quit", "Quit"), Binding("ctrl+c", "quit", "Quit", priority=True)]

    def __init__(self, workspace: Path, detect: Callable[[Harness], str | None] = _detect) -> None:
        super().__init__()
        self.workspace = workspace
        self.current = read_active_harness(workspace)
        self._detect = detect
        # harness name -> version; a name is missing while its detection still runs
        self.versions: dict[str, str | None] = {}
        # index this app last highlighted on its own; any other highlight is the user's choice
        self._auto_highlight: int | None = None
        self._steps: Iterator[str] | None = None
        self._timer: Timer | None = None
        self._lines: list[tuple[str, str]] = []  # (status, text): status is "running", "done" or "failed"

    # -- layout

    def compose(self) -> ComposeResult:
        with Horizontal(id="root"):
            yield Static(render_dino("welcome"), id="dino")
            with Vertical(id="right"):
                with ContentSwitcher(initial="welcome"):
                    yield from self._welcome()
                    yield from self._select()
                    yield from self._nothing()
                    yield from self._progress()
                    yield from self._done()

    def _welcome(self) -> ComposeResult:
        with Vertical(id="welcome"):
            yield Static("Welcome to EvoTrias", classes="title")
            if self.current:
                yield Static(
                    f"This directory is already a {self.current} workspace.\n"
                    'Continue to set it up for another harness, or quit and run "evotrias" to start evolving.',
                    classes="warn",
                )
            else:
                yield Static(f"This will turn\n  {self.workspace}\ninto an EvoTrias workspace.", classes="body")
            with Horizontal(classes="buttons"):
                yield Button("Continue", id="continue")
                yield Button("Quit", id="quit")

    def _select(self) -> ComposeResult:
        with Vertical(id="select"):
            yield Static("Choose a harness", classes="title")
            yield Static("Detected on this machine:", classes="dim")
            yield OptionList(*(Option(*self._option(h)) for h in HARNESSES), id="harnesses")
            yield Static("↑/↓ to move · Enter to select · Esc to quit", classes="dim")

    def _nothing(self) -> ComposeResult:
        with Vertical(id="nothing"):
            yield Static("Nothing to set up", classes="title")
            yield Static("", id="nothing-body", classes="err")
            with Horizontal(classes="buttons"):
                yield Button("Okay", id="okay")

    def _progress(self) -> ComposeResult:
        with Vertical(id="progress"):
            yield Static("Setting up workspace", classes="title")
            yield Static("", id="steps")
            with Horizontal(classes="buttons", id="failed-buttons"):
                yield Button("Okay", id="failed")

    def _done(self) -> ComposeResult:
        with Vertical(id="done"):
            yield Static("Workspace ready.", classes="ok")
            yield Static("", id="done-body", classes="body")
            with Horizontal(classes="buttons"):
                yield Button("Let's go!", id="exit")

    # -- harness choice

    def _option(self, harness: Harness) -> tuple[Text, str, bool]:
        """(label, id, disabled) of one row in the choice list."""
        label = Text()
        if harness.name == self.current:
            label.append(f"  {harness.name:<10}", style=FAINT)
            label.append("current", style=AMBER)
            return label, harness.name, True
        if harness.name not in self.versions:
            label.append(f"  {harness.name:<10}detecting…", style=FAINT)
            return label, harness.name, True
        version = self.versions[harness.name]
        if version is None:
            label.append(f"  {harness.name:<10}not found", style=FAINT)
            return label, harness.name, True
        label.append(f"  {harness.name:<10}", style=f"bold {CYAN}")
        label.append(version, style=DIM)
        return label, harness.name, False

    def _selectable(self) -> list[str]:
        return [h.name for h in HARNESSES if h.name != self.current and self.versions.get(h.name)]

    def on_mount(self) -> None:
        self.query_one("#failed-buttons").display = False
        self.query_one("#continue", Button).focus()
        for harness in HARNESSES:
            self.run_worker(partial(self._detect_in_thread, harness), thread=True)

    def _detect_in_thread(self, harness: Harness) -> None:
        version = self._detect(harness)
        self.call_from_thread(self._detected, harness, version)

    def _detected(self, harness: Harness, version: str | None) -> None:
        self.versions[harness.name] = version
        harnesses = self.query_one("#harnesses", OptionList)
        label, name, disabled = self._option(harness)
        harnesses.replace_option_prompt(name, label)
        if disabled:
            harnesses.disable_option(name)
        else:
            harnesses.enable_option(name)
        if self._page() == "select":
            self._settle_select()

    def _page(self) -> str | None:
        return self.query_one(ContentSwitcher).current

    def _show(self, page: str, mood: str) -> None:
        self.query_one(ContentSwitcher).current = page
        self.query_one("#dino", Static).update(render_dino(mood))

    def _settle_select(self) -> None:
        """On the choice page: keep the highlight on the first choice until the user moves it, or
        give up once every detection has finished without a choice."""
        selectable = self._selectable()
        harnesses = self.query_one("#harnesses", OptionList)
        if selectable:
            if harnesses.highlighted in (None, self._auto_highlight):
                harnesses.highlighted = self._auto_highlight = harnesses.get_option_index(selectable[0])
            harnesses.focus()
        elif len(self.versions) == len(HARNESSES):
            self._show_nothing()

    def _show_nothing(self) -> None:
        if any(self.versions.values()):
            message = (
                f"This workspace is already set up for {self.current}, the only harness found.\n"
                'Run "evotrias" to start evolving.'
            )
        else:
            message = 'No supported harness found.\nInstall one first, then re-run "evotrias setup".'
        self.query_one("#nothing-body", Static).update(message)
        self._show("nothing", "error")
        self.query_one("#okay", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "continue":
            self._show("select", "ask")
            self._settle_select()
        elif event.button.id == "failed":
            self.exit(1)
        else:
            self.exit(0)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        harness = get_harness(str(event.option.id))
        self._steps = install(harness, self.workspace)
        self._show("progress", "ask")
        self._timer = self.set_interval(STEP_INTERVAL_S, partial(self._tick, harness))

    # -- set up

    def _tick(self, harness: Harness) -> None:
        if self._steps is None:
            return
        if self._lines and self._lines[-1][0] == "running":
            self._lines[-1] = ("done", self._lines[-1][1])
        try:
            label = next(self._steps)
        except StopIteration:
            self._finish(harness)
            return
        except Exception as exc:  # shown to the user; the steps already done stay on disk
            self._fail(exc)
            return
        self._lines.append(("running", label))
        self._render_steps()

    def _render_steps(self) -> None:
        text = Text()
        for status, line in self._lines:
            if status == "done":
                text.append("  ✓ ", style=TEAL)
                text.append(line + "\n", style=INK)
            elif status == "running":
                text.append("  … ", style=CYAN)
                text.append(line + "\n", style=DIM)
            else:
                text.append("  ✗ ", style=CLAY)
                text.append(line + "\n", style=CLAY)
        self.query_one("#steps", Static).update(text)

    def _stop_steps(self) -> None:
        self._steps = None
        if self._timer is not None:
            self._timer.stop()

    def _fail(self, exc: Exception) -> None:
        self._stop_steps()
        self._lines.append(("failed", f"{type(exc).__name__}: {exc}"))
        self._render_steps()
        self.query_one("#dino", Static).update(render_dino("error"))
        self.query_one("#failed-buttons").display = True
        self.query_one("#failed", Button).focus()

    def _finish(self, harness: Harness) -> None:
        self._stop_steps()
        self._render_steps()
        self.query_one("#done-body", Static).update(
            f"Copy any project-level skills you need into\n  {harness.skills_dir}/\n"
            'then run "evotrias" to start evolving.'
        )
        self._show("done", "done")
        self.query_one("#exit", Button).focus()

    async def action_quit(self) -> None:
        # once writing has started, let it finish (it takes a moment) rather than stop halfway
        if self._steps is None:
            self.exit(0)


def setup_tui(workspace: Path) -> int:
    """Run the interactive setup in ``workspace``; 0 unless setting up failed."""
    return SetupApp(workspace).run() or 0

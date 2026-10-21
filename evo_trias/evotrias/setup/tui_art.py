# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Static art for the setup TUI: palette (design.md §6.5), dino and banner (§6.4)."""

from __future__ import annotations

from rich.text import Text

# palette, from the website's CSS variables
BG = "#03060a"  # --bg
PANEL = "#070d13"  # --bg2
INK = "#e8f1ef"  # --ink
DIM = "#8ea3a0"  # --dim
FAINT = "#56666a"  # --faint
CYAN = "#4fd1e8"  # --cyan
TEAL = "#22a889"  # --teal
AMBER = "#e0a24a"  # --amber
CLAY = "#d0552b"  # --clay
DINO = "#41a4bc"  # dino strokes, from the website SVG
LINE = "rgba(120,220,235,0.28)"  # --line-2
HIGHLIGHT = "#12313b"  # highlighted option (cyan darkened; not on the website)
HOVER = "#0c1d24"  # hovered option

DINO_ART = [
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢰⣛⣶⣤⣤⣠⡴⠖⠒⠶⣄⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢰⣻⠟⠉⠀⠀⠀⠀⠀⠀⠀⠀⠘⡆",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⣼⠇⠀⠀⠀⠀{eye}⠀⠀⠀⠀⠀⠀⡇",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠻⣾⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣼⠃",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⢻⡀⠀⠀⠀⠀⠀⠀⠀⠀⣠⠞⠁⠀",
    "⠀⣀⡀⠀⠀⠀⠀⠀⠀⢨⡿⡇⠀⠀⠀⠀⠀⠀⢀⡼⠁⠀⠀⠀",
    "⢸⠋⣧⠀⠀⠀⠀⠀⠀⣹⣤⡇⠀⠀⠀⠀⠀⠀⢸⡃⠀⠀⠀⠀",
    "⢸⠀⢹⡄⠀⠀⠀⣀⣾⣁⡟⠀⠀⠀⢀⡀⠀⠀⢸⢧⠀⠀⠀⠀",
    "⠸⡆⠀⠻⣦⡀⢸⣉⣽⠋⠀⠀⡄⠀⠀⠹⣄⠀⡟⢘⡇⠀⠀⠀",
    "⠀⢻⡄⠀⠀⠉⠉⣹⠃⠀⠀⠀⣙⣦⣄⣀⡽⠀⢧⡼⠛⢳⡄⠀",
    "⠀⠀⠹⢦⡀⠀⠀⣿⠀⠀⠀⠘⠁⠀⠀⢻⡀⠀⣼⢁⡤⢼⡇⠀",
    "⠀⠀⠀⠀⠙⠲⢤⣜⣦⡀⠀⠀⠀⢰⠋⣿⣡⣾⣁⣸⣤⠟⠀⠀",
    "⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⠒⠦⠤⠼⠾⠋⠀⠀⠀⠀⠀⠀⠀⠀",
]
BANNER = [
    "╔═╗┬  ┬┌─┐╔╦╗┬─┐┬┌─┐┌─┐",
    "║╣ └┐┌┘│ │ ║ ├┬┘│├─┤└─┐",
    "╚═╝ └┘ └─┘ ╩ ┴└─┴┴ ┴└─┘",
]
# The left column is fixed at 30 columns, 1 of them right padding; the right frame is exactly as
# tall as the art.
LEFT_WIDTH = 30
LEFT_HEIGHT = len(DINO_ART) + len(BANNER)
# mood -> (eye, symbol beside the head); single-width characters only
MOODS = {
    "welcome": ("o", "Hi!"),
    "ask": ("O", "?"),
    "done": ("^", "✦"),
    "error": ("x", ""),
}


def _lerp(c1: str, c2: str, t: float) -> str:
    a = [int(c1[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def _gradient(t: float) -> str:
    # website --grad: cyan -> teal (55%) -> amber
    return _lerp(CYAN, TEAL, t / 0.55) if t <= 0.55 else _lerp(TEAL, AMBER, (t - 0.55) / 0.45)


def render_dino(mood: str) -> Text:
    """The whole left column: dino with the mood's eye and symbol, then the gradient banner."""
    eye, symbol = MOODS[mood]
    text = Text()
    for i, line in enumerate(DINO_ART):
        if "{eye}" in line:
            before, _, after = line.partition("{eye}")
            text.append(before, style=DINO)
            text.append(eye, style=f"bold {INK}")
            text.append(after, style=DINO)
        else:
            text.append(line, style=DINO)
        if i == 0 and symbol:
            text.append(f" {symbol}", style=f"bold {CYAN}")
        text.append("\n")
    width = max(len(b) for b in BANNER)
    for line in BANNER:
        for col, ch in enumerate(line):
            text.append(ch, style=f"bold {_gradient(col / (width - 1))}")
        text.append("\n")
    text.rstrip()
    return text

# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Tests for evotrias.setup.tui_art: the left column fits its width in every mood."""

from __future__ import annotations

import pytest

from evotrias.setup.tui_art import BANNER, DINO_ART, LEFT_HEIGHT, LEFT_WIDTH, MOODS, render_dino


@pytest.mark.parametrize("mood", MOODS)
def test_left_column_fits_in_every_mood(mood):
    lines = render_dino(mood).plain.split("\n")

    assert len(lines) == LEFT_HEIGHT == 16
    # 1 column of the left column is right padding
    assert max(len(line) for line in lines) <= LEFT_WIDTH - 1


@pytest.mark.parametrize(("mood", "eye", "symbol"), [(m, e, s) for m, (e, s) in MOODS.items()])
def test_mood_changes_only_the_eye_and_the_symbol(mood, eye, symbol):
    lines = render_dino(mood).plain.split("\n")

    eye_row = next(i for i, line in enumerate(DINO_ART) if "{eye}" in line)
    assert lines[eye_row] == DINO_ART[eye_row].replace("{eye}", eye)
    assert lines[0] == (f"{DINO_ART[0]} {symbol}" if symbol else DINO_ART[0])
    others = [i for i in range(len(DINO_ART)) if i not in (0, eye_row)]
    assert [lines[i] for i in others] == [DINO_ART[i] for i in others]
    assert lines[len(DINO_ART) :] == BANNER


def test_eyes_and_symbols_are_single_width():
    # wide or ambiguous characters would shift the dino in some terminals
    for eye, symbol in MOODS.values():
        assert len(eye) == 1 and eye.isascii()
        assert symbol.isascii() or symbol == "✦"

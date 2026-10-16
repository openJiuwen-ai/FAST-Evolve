"""All supported harnesses, in the order setup lists them."""

from __future__ import annotations

from evotrias.setup.harness.base import Harness
from evotrias.setup.harness.claude import ClaudeHarness
from evotrias.setup.harness.opencode import OpencodeHarness

HARNESSES: list[Harness] = [ClaudeHarness(), OpencodeHarness()]


def get_harness(name: str) -> Harness:
    """Harness registered under name; ValueError listing the known names otherwise."""
    for harness in HARNESSES:
        if harness.name == name:
            return harness
    raise ValueError(f"unknown harness {name!r}; expected one of {[h.name for h in HARNESSES]}")

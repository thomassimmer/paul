"""Small text helpers shared by the profiler's inputs."""

from __future__ import annotations

import re

_BLANK_LINES = re.compile(r"\n{3,}")
_TRAILING_SPACES = re.compile(r"[ \t]+$", re.MULTILINE)


def tidy_text(text: str) -> str:
    """Normalise extracted or pasted text so downstream prompts stay small."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _TRAILING_SPACES.sub("", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


def split_list(text: str) -> list[str]:
    """Split a user-typed list on commas or newlines, dropping empties."""
    items = re.split(r"[,\n;]+", text or "")
    return [item.strip() for item in items if item.strip()]


def split_lines(text: str) -> list[str]:
    """Split on newlines only: one line, one item, commas and all.

    What the highlights of an experience need. A sentence of that list can
    legitimately contain a comma ("Cut latency, from 900ms to 120ms"), so
    ``split_list`` would cut it in two.
    """
    return [line.strip() for line in (text or "").splitlines() if line.strip()]

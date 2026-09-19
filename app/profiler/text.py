"""Small text helpers shared by the profiler's inputs."""

from __future__ import annotations

import re

_BLANK_LINES = re.compile(r"\n{3,}")
_TRAILING_SPACES = re.compile(r"[ \t]+$", re.MULTILINE)

# "60%", "3x", "12 engineers", "2 months", "1.2M €". Best effort on purpose.
_METRIC_RE = re.compile(
    r"""
    [-+]?\d+(?:[.,]\d+)*\s?
    (?:
        %|x|×
        |(?:ms|min|h|k|M|bn)\b
        |(?:hours?|days?|weeks?|months?|years?|users?|customers?|clients?
          |requests?|engineers?|people|teams?|services?)\b
    )
    (?:\s?(?:€|\$|£))?          # a currency may follow the magnitude: "1.2M €"
    """,
    re.IGNORECASE | re.VERBOSE,
)


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


def extract_metrics(text: str) -> list[str]:
    """Return the measurable bits of a sentence, in order and deduplicated.

    Used when an achievement is typed or answered: the writer then has real
    numbers to work with instead of a sentence it has to re-read.
    """
    seen: set[str] = set()
    metrics: list[str] = []
    for match in _METRIC_RE.finditer(text or ""):
        metric = " ".join(match.group(0).split())
        if metric.lower() not in seen:
            seen.add(metric.lower())
            metrics.append(metric)
    return metrics

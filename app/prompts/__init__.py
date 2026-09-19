"""Prompts live in files, not in code.

Each prompt is a plain Markdown file under this package, loaded by name. The
structured part of a call (the JSON schema) is added by ``app.llm``, so prompt
files stay free of braces and placeholders.
"""

from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).parent


class PromptError(RuntimeError):
    """The prompt file is missing."""


def load_prompt(name: str) -> str:
    """Return the contents of ``app/prompts/<name>``."""
    path = PROMPTS_DIR / name
    if not path.is_file():
        raise PromptError(f"Unknown prompt: {name}")
    return path.read_text(encoding="utf-8").strip()

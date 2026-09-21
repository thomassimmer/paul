"""Prompts live in files, not in code.

Each prompt is a plain Markdown file under this package, loaded by name. The
structured part of a call (the JSON schema) is added by ``app.llm``, so prompt
files stay free of braces and placeholders.

A user can override any prompt: a file of the same relative name under
``data/prompts/`` takes precedence over the packaged default, and deleting that
file brings the default back. The settings page edits every prompt inline, and
the same files can be written by hand.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

from app.config import DATA_DIR

PROMPTS_DIR = Path(__file__).parent
OVERRIDE_DIR = DATA_DIR / "prompts"
SUFFIX = ".md"

# A human label for each prompt, shown on the settings page. A prompt whose name
# is missing here falls back to its path.
LABELS: dict[str, str] = {
    "offers/extract_offer.md": "Read an offer",
    "profiler/draft_profile.md": "Draft the profile from a CV",
    "profiler/interview.md": "The interview",
    "ranking/eliminate.md": "Apply the elimination rules",
    "ranking/score.md": "Offer scoring",
    "templates/assign_roles.md": "Read a template's roles",
    "writer/form_answers.md": "Form answers",
    "writer/keywords.md": "Keyword coverage (ATS)",
    "writer/tailor_cv.md": "Tailor the CV",
    "writer/write_letter.md": "Cover letter",
}


class PromptError(RuntimeError):
    """The prompt file is missing, or the name is not a known prompt."""


def names() -> list[str]:
    """Every packaged prompt, as a relative path, sorted."""
    return sorted(
        path.relative_to(PROMPTS_DIR).as_posix() for path in PROMPTS_DIR.rglob(f"*{SUFFIX}")
    )


def _default_path(name: str) -> Path:
    """The packaged path of ``name``, refused when the name is not a known prompt.

    The whitelist matters: a name that arrives from a form must never be joined
    to a directory unchecked, or it could point anywhere on disk.
    """
    if name not in names():
        raise PromptError(f"Unknown prompt: {name}")
    return PROMPTS_DIR / name


def override_path(name: str) -> Path:
    """Where the user's override of ``name`` lives, if they wrote one."""
    _default_path(name)  # validate the name before using it in a path
    return OVERRIDE_DIR / name


def default(name: str) -> str:
    """The packaged text of ``name``, whatever the user has overridden."""
    return _default_path(name).read_text(encoding="utf-8").strip()


def is_overridden(name: str) -> bool:
    """True when the user replaced ``name`` with their own file."""
    return override_path(name).is_file()


def load_prompt(name: str) -> str:
    """The text actually used for ``name``: the override when there is one."""
    path = override_path(name)
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return default(name)


def save_override(name: str, text: str) -> None:
    """Store an override for ``name``; blank text is refused, not written.

    A prompt with nothing in it would quietly change what the model is asked to
    do, so an empty box means "leave the prompt alone", not "blank it".
    """
    path = override_path(name)
    content = text.strip()
    if not content:
        raise PromptError("A prompt cannot be empty.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + "\n", encoding="utf-8")


def reset(name: str) -> None:
    """Drop the override of ``name``, so the packaged default is used again."""
    override_path(name).unlink(missing_ok=True)


def digest(used: Iterable[str]) -> str:
    """A short hash of the text in use for the prompts named in ``used``.

    Feeds the ranking fingerprint: an edited scoring prompt must mark the stored
    scores out of date, exactly like a changed rule or model does.
    """
    hasher = hashlib.sha256()
    for name in sorted(used):
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(load_prompt(name).encode("utf-8"))
        hasher.update(b"\0")
    return hasher.hexdigest()[:16]


def overview() -> list[dict]:
    """One entry per prompt, for the settings page."""
    return [
        {
            "name": name,
            "label": LABELS.get(name, name),
            "overridden": is_overridden(name),
            "text": load_prompt(name),
        }
        for name in names()
    ]

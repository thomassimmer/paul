"""LLM step: turn a free-text answer into structured achievements.

The deterministic split in ``interview.py`` already works, so this pass is an
improvement, never a dependency: when no model is configured, or when the model
answers something that cannot be verified, the caller keeps the plain split.

"Improvement" must not mean "invention", so the model has to quote the answer it
worked from and every number it uses must already be in that answer. A result
that fails a check is rejected as a whole rather than trimmed, because dropping
one item would silently lose a piece of what the candidate wrote.
"""

from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel, Field

from app.config import Settings
from app.llm import complete_structured
from app.models import Achievement, Experience
from app.prompts import load_prompt

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_WHITESPACE = re.compile(r"\s+")


class StructuredAchievement(BaseModel):
    """One achievement proposed by the model, with its provenance."""

    text: str = ""
    source: str = ""  # a verbatim excerpt of the answer, checked by code
    metrics: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class StructuredAchievements(BaseModel):
    achievements: list[StructuredAchievement] = Field(default_factory=list)


def _fold(text: str) -> str:
    """Case- and accent-insensitive, whitespace collapsed."""
    decomposed = unicodedata.normalize("NFD", text or "")
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _WHITESPACE.sub(" ", without_accents.casefold()).strip()


def _compact(text: str) -> str:
    return _fold(text).replace(" ", "")


def _canonical(text: str) -> str:
    """Compact form with a decimal comma normalised, for comparing numbers."""
    return _compact(text).replace(",", ".")


def _present(value: str, answer_compact: str) -> bool:
    return bool(value.strip()) and _compact(value) in answer_compact


def _is_grounded(item: StructuredAchievement, answer_folded: str, answer_canonical: str) -> bool:
    """True when the item quotes the answer and adds no number of its own."""
    if not item.source.strip() or _fold(item.source) not in answer_folded:
        return False
    return all(
        number.replace(",", ".") in answer_canonical for number in _NUMBER.findall(item.text or "")
    )


def accept_achievements(
    items: list[StructuredAchievement], answer: str
) -> list[Achievement] | None:
    """Keep what the answer supports, or ``None`` to fall back to the plain split.

    ``metrics`` and ``skills`` that the answer does not actually contain are
    dropped: they could only ever add a claim. An achievement whose source or
    numbers cannot be verified rejects the whole batch, because the caller can
    still keep the candidate's own wording instead.
    """
    answer_folded = _fold(answer)
    answer_compact = _compact(answer)
    answer_canonical = _canonical(answer)

    accepted: list[Achievement] = []
    for item in items:
        text = item.text.strip()
        if not text:
            continue
        if not _is_grounded(item, answer_folded, answer_canonical):
            return None
        accepted.append(
            Achievement(
                text=text,
                metrics=[m.strip() for m in item.metrics if _present(m, answer_compact)],
                skills=[s.strip() for s in item.skills if _present(s, answer_compact)],
            )
        )
    return accepted or None


def _user_content(experience: Experience, answer: str) -> str:
    label = " — ".join(part for part in (experience.company, experience.title) if part)
    header = f"Experience: {label}" if label else "Experience: (unspecified)"
    return f"{header}\n\nCandidate's answer:\n{answer}"


async def structure_achievements(
    settings: Settings, *, answer: str, experience: Experience
) -> list[Achievement] | None:
    """Ask the model to split an answer into achievements.

    Returns ``None`` when the model is unavailable or its answer cannot be
    verified; the caller then falls back to the deterministic split.
    """
    answer = (answer or "").strip()
    if not answer or not settings.model.strip():
        return None

    result = await complete_structured(
        settings,
        schema=StructuredAchievements,
        content=_user_content(experience, answer),
        system=load_prompt("profiler/structure_achievements.md"),
    )
    return accept_achievements(result.achievements, answer)

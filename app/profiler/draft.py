"""LLM step: turn CV text into a first profile draft."""

from __future__ import annotations

from app.config import Settings
from app.llm import complete_structured
from app.models import ProfileDraft
from app.prompts import load_prompt


async def draft_profile(settings: Settings, cv_text: str) -> ProfileDraft:
    """Return a draft profile built only from what the CV says.

    The schema has no ``facts`` and no ``preferences``, so the model cannot fill
    what only the candidate may answer.
    """
    return await complete_structured(
        settings,
        schema=ProfileDraft,
        content=cv_text,
        system=load_prompt("profiler/draft_profile.md"),
    )

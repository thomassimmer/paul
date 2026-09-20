"""Profiler orchestration, kept out of the HTTP layer.

The router deals with requests and redirects; what actually happens on an
import (extract, draft, preserve what is yours, save) lives here.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.llm import LLMError
from app.models import Profile
from app.profiler import cv, interview, store, structure
from app.profiler.draft import draft_profile
from app.profiler.text import tidy_text

FACT_LABELS = {
    "work_authorization": "work authorization",
    "notice_period": "notice period",
    "salary_expectation": "salary expectation",
}


@dataclass
class ImportOutcome:
    profile: Profile
    drafted: bool
    notice: str
    level: str = "ok"


@dataclass
class PreparedImport:
    """What the local half of an import produced, ready for the model."""

    cv_text: str
    source: str
    existing: Profile | None


def read_cv(*, filename: str = "", data: bytes | None = None, text: str = "") -> PreparedImport:
    """The local half of an import: extract the CV text and save it.

    Raises ``cv.CvError`` when nothing usable can be read, which is cheap enough
    to answer on the page before the model call is dispatched.
    """
    if text and text.strip():
        cv_text = tidy_text(text)
        source = "the pasted text"
    elif data:
        cv_text = cv.extract_text(filename or "cv", data)
        source = filename or "the uploaded file"
    else:
        raise cv.CvError("Paste your CV text or choose a file.")

    store.save_cv_text(cv_text)

    existing = None
    try:
        existing = store.load_profile()
    except store.ProfileError:
        existing = None  # a broken file must not block a fresh import
    return PreparedImport(cv_text=cv_text, source=source, existing=existing)


async def draft_profile_into(settings: Settings, prepared: PreparedImport) -> ImportOutcome:
    """The model half: draft the profile from the CV text and store it."""
    profile = Profile()
    drafted = False

    if settings.model.strip():
        try:
            profile = (await draft_profile(settings, prepared.cv_text)).to_profile()
            drafted = True
            notice, level = f"Profile drafted from {prepared.source}.", "ok"
        except LLMError as exc:
            notice = (
                f"The model could not draft the profile ({exc}). The CV text is saved: "
                "fix the model in Settings and import again, or fill the profile by hand."
            )
            level = "error"
    else:
        notice = (
            "No model configured: the CV text is saved, but the profile was left empty. "
            "Set a model in Settings to draft it automatically, or fill it in by hand."
        )
        level = "warning"

    # Facts and preferences are answered by you, never by an import: keep them.
    if prepared.existing is not None:
        profile.facts = prepared.existing.facts
        profile.preferences = prepared.existing.preferences

    profile = store.save_profile(profile)
    return ImportOutcome(profile=profile, drafted=drafted, notice=notice, level=level)


async def import_cv(
    settings: Settings,
    *,
    filename: str = "",
    data: bytes | None = None,
    text: str = "",
) -> ImportOutcome:
    """Extract a CV, draft a profile from it, and store both.

    Raises ``cv.CvError`` when nothing usable can be read.
    """
    return await draft_profile_into(settings, read_cv(filename=filename, data=data, text=text))


async def apply_interview_answer(
    settings: Settings, profile: Profile, key: str, answer: str
) -> tuple[Profile, str | None]:
    """Merge an interview answer, structuring it with the model when there is one.

    Returns ``(profile, notice)``; ``notice`` is set only when the model was
    asked for a structured breakdown and could not be trusted, which is worth
    telling the user about. The deterministic split is always the fallback.
    """
    experience = interview.is_achievements_target(profile, key)
    achievements = None
    notice = None

    if experience is not None and settings.model.strip() and answer.strip():
        try:
            achievements = await structure.structure_achievements(
                settings, answer=answer, experience=experience
            )
        except LLMError as exc:
            notice = (
                f"The model could not structure this answer ({exc}). "
                "Your text was kept, one line per achievement."
            )
        if achievements is None and notice is None:
            notice = (
                "The model's breakdown could not be verified against your text, so it was "
                "discarded: your answer was kept as written, one line per achievement."
            )

    updated = interview.apply_answer(profile, key, answer, achievements=achievements)
    return updated, notice


def summary(profile: Profile | None) -> dict[str, object]:
    """Small derived counters for the dashboard and the profile page."""
    if profile is None:
        return {
            "has_profile": False,
            "experiences": 0,
            "achievements": 0,
            "skills": 0,
            "missing_facts": [],
        }
    missing = [
        label
        for field, label in FACT_LABELS.items()
        if not str(getattr(profile.facts, field, "") or "").strip()
    ]
    return {
        "has_profile": True,
        "experiences": len(profile.experiences),
        "achievements": sum(len(e.achievements) for e in profile.experiences),
        "skills": sum(len(items) for items in profile.skills.values()),
        "missing_facts": missing,
    }

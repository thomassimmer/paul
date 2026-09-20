"""Profiler orchestration, kept out of the HTTP layer.

The router deals with requests and redirects; what actually happens on an import
(extract, draft, preserve what is yours, save) and on an interview turn (ask,
write, remember) lives here.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.llm import LLMError
from app.models import Profile
from app.profiler import cv, interview, store
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
    # The interview was built from the profile this replaced, so its questions are
    # about experiences and gaps that may no longer exist: start it over.
    store.forget_questions()
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


async def next_question(settings: Settings, profile: Profile) -> interview.Turn:
    """The question to ask now, computed from the profile as it stands.

    Nothing is stored: closing and reopening the interview asks it again from the
    current profile, which is what lets the candidate stop and come back later.
    """
    return await interview.ask(settings, profile, asked=store.asked_questions())


async def answer_question(
    settings: Settings, profile: Profile, question: str, answer: str
) -> tuple[interview.DraftResult, interview.Turn]:
    """Write what one answer says, and ask the next question.

    The two come from a single model call, so an exchange costs one round trip.
    The question is marked as asked — unless part of the answer could not be
    written: then the interview must be able to come back to it, since the
    candidate only has the page to rephrase what was lost.
    """
    turn = await interview.ask(
        settings, profile, asked=store.asked_questions(), question=question, answer=answer
    )
    result = interview.apply_turn(profile, turn, answer)
    store.save_profile(result.profile)
    if not result.dropped:
        store.remember_question(question)
    return result, turn


async def skip_question(
    settings: Settings, profile: Profile, question: str
) -> interview.Turn:
    """Remember a question the candidate would rather not answer, and move on."""
    store.remember_question(question)
    return await interview.ask(
        settings, profile, asked=store.asked_questions(), question=question, answer=""
    )


def summary(profile: Profile | None) -> dict[str, object]:
    """Small derived counters for the dashboard and the profile page."""
    if profile is None:
        return {
            "has_profile": False,
            "experiences": 0,
            "highlights": 0,
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
        "highlights": sum(len(e.highlights) for e in profile.experiences),
        "skills": sum(len(items) for items in profile.skills.values()),
        "missing_facts": missing,
    }

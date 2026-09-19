"""The LLM steps of the writer, and the guards around what comes back.

Every call returns a validated object, but a validation our models define, not the
one the renderer needs: a model may answer ``role: "skills"`` or ``"Title"`` where
the template knows ``skill_line`` and ``section_title``. Rather than pay a retry
for a spelling, the roles are repaired here, in code, against the exact tuple the
renderer uses. ``achievement_ids`` are cleaned the same way, so the grounding check
only ever sees ids that were actually written.

The prompts are the ones that carry the rule ("use only the profile"); this module
is where the rule is enforced.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.config import Settings
from app.llm import complete_structured
from app.models import (
    CV_ROLES,
    LETTER_ROLES,
    AnswersDraft,
    CvDraft,
    DraftAnswer,
    DraftLine,
    LetterDraft,
    Offer,
    Profile,
    TemplateBlueprint,
)
from app.prompt_context import offer_text, output_language_text, profile_text
from app.prompts import load_prompt

DEFAULT_ROLE = "body_text"

# Spellings models fall back to, mapped to the roles the renderer knows.
ROLE_ALIASES: dict[str, str] = {
    "skill": "skill_line",
    "skills": "skill_line",
    "skills_line": "skill_line",
    "skillline": "skill_line",
    "title": "section_title",
    "section": "section_title",
    "heading": "section_title",
    "header": "section_title",
    "paragraph": "body_text",
    "text": "body_text",
    "summary": "body_text",
    "profile": "body_text",
    "bullet_point": "bullet",
    "bullets": "bullet",
    "list_item": "bullet",
    "dates": "entry_dates",
    "subtitle": "entry_subtitle",
    "entry": "entry_title",
    "company": "entry_title",
    "contact_line": "contact",
    "greeting": "salutation",
    "signoff": "closing",
    "sign_off": "closing",
    "signature_line": "signature",
}


def normalize_role(role: str, allowed: Sequence[str]) -> str:
    """The closest role the renderer accepts, never an empty one."""
    cleaned = (role or "").strip().casefold().replace(" ", "_").replace("-", "_")
    if cleaned in allowed:
        return cleaned
    alias = ROLE_ALIASES.get(cleaned, "")
    if alias in allowed:
        return alias
    return DEFAULT_ROLE if DEFAULT_ROLE in allowed else allowed[0]


def clean_ids(ids: Iterable[str]) -> list[str]:
    """Drop blanks and duplicates, keeping the order the model wrote."""
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in ids:
        value = (item or "").strip()
        if value and value not in seen:
            seen.add(value)
            cleaned.append(value)
    return cleaned


def normalize_lines(lines: list[DraftLine], allowed: Sequence[str]) -> list[DraftLine]:
    """Repair roles and drop the empty lines, nothing else.

    The text is left exactly as the model wrote it: rewriting it here would make
    the grounding report point at something the user never saw.
    """
    result: list[DraftLine] = []
    for line in lines:
        text = line.text.strip()
        if not text:
            continue
        result.append(
            DraftLine(
                role=normalize_role(line.role, allowed),
                text=text,
                achievement_ids=clean_ids(line.achievement_ids),
            )
        )
    return result


def fit_length(text: str, max_length: int | None) -> str:
    """Cut a generated answer to a form's character limit, at a word boundary.

    The prompt asks for it, but a limit stated in prose is advice where the field
    itself is a hard wall: what overflows is simply refused by the site, so the
    code makes sure it cannot happen.
    """
    if not max_length or max_length <= 0 or len(text) <= max_length:
        return text
    cut = text[:max_length]
    head, sep, _ = cut.rpartition(" ")
    return (head if sep and head else cut).rstrip(" ,;:.")


def template_text(blueprint: TemplateBlueprint) -> str:
    """What the model needs to know about the candidate's own template."""
    roles = list(dict.fromkeys(block.role for block in blueprint.blocks if block.role))
    sections = [
        block.text.strip()
        for block in blueprint.blocks
        if block.role == "section_title" and block.text.strip()
    ]
    lines = ["The document is rendered in the candidate's own template."]
    if roles:
        lines.append("It has a style for these roles: " + ", ".join(roles) + ".")
    if sections:
        lines.append(
            "Its sections are named: " + ", ".join(sections) + ". Prefer these names for "
            "section_title lines, and only add a section the profile really fills."
        )
    return "\n".join(lines)


def _request(
    *,
    profile: Profile,
    offer: Offer,
    blueprint: TemplateBlueprint,
    settings: Settings,
    target_pages: int,
    instruction: str = "",
) -> str:
    parts = [
        "## Candidate profile",
        profile_text(profile),
        "## Offer",
        offer_text(offer),
        "## Output",
        output_language_text(settings, offer),
        f"Target length: {target_pages} page(s).",
        "## Template",
        template_text(blueprint),
    ]
    if instruction.strip():
        parts += ["## Additional instruction", instruction.strip()]
    return "\n\n".join(parts) + "\n"


async def tailor_cv(
    settings: Settings,
    *,
    offer: Offer,
    profile: Profile,
    blueprint: TemplateBlueprint,
    target_pages: int,
    instruction: str = "",
) -> list[DraftLine]:
    """Draft the CV lines. The grounding check is the caller's job."""
    draft = await complete_structured(
        settings,
        schema=CvDraft,
        content=_request(
            profile=profile,
            offer=offer,
            blueprint=blueprint,
            settings=settings,
            target_pages=target_pages,
            instruction=instruction,
        ),
        system=load_prompt("writer/tailor_cv.md"),
    )
    return normalize_lines(draft.lines, CV_ROLES)


async def write_letter(
    settings: Settings,
    *,
    offer: Offer,
    profile: Profile,
    blueprint: TemplateBlueprint,
    target_pages: int,
    instruction: str = "",
) -> list[DraftLine]:
    draft = await complete_structured(
        settings,
        schema=LetterDraft,
        content=_request(
            profile=profile,
            offer=offer,
            blueprint=blueprint,
            settings=settings,
            target_pages=target_pages,
            instruction=instruction,
        ),
        system=load_prompt("writer/write_letter.md"),
    )
    return normalize_lines(draft.lines, LETTER_ROLES)


def questions_text(questions: list[tuple[str, int | None]]) -> str:
    """The open questions, numbered, with the limit each one carries."""
    lines = []
    for index, (question, max_length) in enumerate(questions, start=1):
        suffix = f" (max_length: {max_length})" if max_length else ""
        lines.append(f"{index}. {question}{suffix}")
    return "\n".join(lines)


async def answer_questions(
    settings: Settings,
    *,
    offer: Offer,
    profile: Profile,
    questions: list[tuple[str, int | None]],
    instruction: str = "",
) -> list[DraftAnswer]:
    """Draft the open questions. Factual ones never reach this function."""
    if not questions:
        return []
    parts = [
        "## Candidate profile",
        profile_text(profile),
        "## Offer",
        offer_text(offer),
        "## Output",
        output_language_text(settings, offer),
        "## Questions to answer",
        questions_text(questions),
    ]
    if instruction.strip():
        parts += ["## Additional instruction", instruction.strip()]

    draft = await complete_structured(
        settings,
        schema=AnswersDraft,
        content="\n\n".join(parts) + "\n",
        system=load_prompt("writer/form_answers.md"),
    )
    return draft.answers

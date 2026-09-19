"""The roles a template block can play.

Kept in one place because three things must agree on them: the blueprint that is
stored, the prompt that asks the model for the roles, and the renderer's
fallbacks when the user's template has no block for a role.
"""

from __future__ import annotations

import re

from app.models import CV_ROLES, LETTER_ROLES, TemplateBlueprint

ROLES = {"cv": CV_ROLES, "letter": LETTER_ROLES}

ROLE_LABELS = {
    "name": "Name",
    "headline": "Headline",
    "contact": "Contact line",
    "section_title": "Section title",
    "entry_title": "Entry title",
    "entry_subtitle": "Entry subtitle",
    "entry_dates": "Dates",
    "bullet": "Bullet",
    "body_text": "Paragraph",
    "skill_line": "Skills line",
    "recipient": "Recipient",
    "date": "Date",
    "salutation": "Salutation",
    "closing": "Closing",
    "signature": "Signature",
    "fixed": "Fixed — kept untouched",
}

# A section the template does not show is still worth rendering, in the closest
# style it does show. The first role that has a prototype wins.
FALLBACKS: dict[str, tuple[str, ...]] = {
    "name": ("headline", "section_title", "body_text"),
    "headline": ("name", "section_title", "body_text"),
    "contact": ("body_text", "headline"),
    "section_title": ("entry_title", "headline", "body_text"),
    "entry_title": ("section_title", "body_text"),
    "entry_subtitle": ("body_text", "entry_dates"),
    "entry_dates": ("body_text",),
    "bullet": ("body_text",),
    "body_text": ("bullet",),
    "skill_line": ("body_text", "bullet"),
    "recipient": ("body_text",),
    "date": ("body_text",),
    "salutation": ("body_text",),
    "closing": ("body_text",),
    "signature": ("name", "body_text"),
    "fixed": (),
}

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"\+?\d[\d ().-]{7,}\d")
_YEARS = re.compile(r"\b(?:19|20)\d{2}\b")
_SALUTATION = re.compile(r"^\s*(dear|bonjour|madame|monsieur|hello)\b", re.IGNORECASE)
_CLOSING = re.compile(
    r"^\s*(sincerely|best regards|kind regards|regards|yours|"
    r"cordialement|bien à vous|bien cordialement)\b",
    re.IGNORECASE,
)
# Section names every CV shares, in the languages the app is likely to meet.
_SECTION_WORDS = {
    "experience",
    "experiences",
    "work experience",
    "education",
    "formation",
    "skills",
    "competences",
    "languages",
    "langues",
    "projects",
    "projets",
    "certifications",
    "summary",
    "profile",
    "profil",
    "interests",
    "centres d interet",
}


def _is_section_name(text: str) -> bool:
    lowered = text.strip().rstrip(":").casefold()
    return lowered in _SECTION_WORDS


def _looks_like_phone(text: str) -> bool:
    """A date range such as "2022 - 2024" reads like a phone number otherwise."""
    for match in _PHONE.finditer(text):
        if sum(character.isdigit() for character in match.group(0)) >= 9:
            return True
    return False


def label(role: str) -> str:
    return ROLE_LABELS.get(role, role or "—")


def prototype_for(blueprint: TemplateBlueprint, role: str) -> str | None:
    """The block XML to copy for a role, following the fallbacks if needed."""
    for candidate in (role, *FALLBACKS.get(role, ())):
        xml = blueprint.prototype(candidate)
        if xml:
            return xml
    return None


def guess_roles(texts: list[str], hints: list[str], kind: str) -> list[str]:
    """A role for every block, without asking a model.

    Used when no model is configured, and as the starting point the user can
    correct: a wrong guess costs a click, a missing page costs more.
    """
    roles: list[str] = []
    inside_entry = False
    for index, text in enumerate(texts):
        hint = hints[index] if index < len(hints) else ""
        role = _guess_one(text, hint, kind, index, inside_entry)
        if role == "section_title":
            inside_entry = False
        elif role == "entry_title":
            inside_entry = True
        roles.append(role)
    return roles


def _guess_one(text: str, hint: str, kind: str, index: int, inside_entry: bool) -> str:
    lowered = hint.lower()
    if _SALUTATION.match(text):
        return "salutation"
    if _CLOSING.match(text):
        return "closing"
    if "list" in lowered or text.startswith(("\u2022", "-", "\u2013", "\u25aa")):
        return "bullet"
    if index == 0:
        return "name"
    # Dates first: a year range looks like a phone number to a loose pattern.
    if _YEARS.search(text) and len(text) < 40:
        return "entry_dates"
    if _EMAIL.search(text) or _looks_like_phone(text):
        return "contact"
    if kind == "letter" and index == 1:
        return "contact"
    if "bold" in lowered and len(text) < 60 and not text.endswith((".", "!", "?")):
        # A short bold line is a title; only the context says which kind.
        if _is_section_name(text) or not inside_entry:
            return "section_title"
        return "entry_title"
    return "body_text"

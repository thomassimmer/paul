"""Stable identifiers for the profile.

The writer cites experience ids, and that is what makes the grounding check
possible: ids must therefore be stable, human-readable and never renumbered.
An id that is already present is always kept as-is.
"""

from __future__ import annotations

import re

from app.models import Experience, Profile

_SEPARATORS = re.compile(r"[^a-z0-9]+")
_YEAR = re.compile(r"(?:19|20)\d{2}")


def slugify(text: str) -> str:
    slug = _SEPARATORS.sub("-", (text or "").strip().lower()).strip("-")
    return slug or "item"


def _unique(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    suffix = 2
    while f"{base}-{suffix}" in taken:
        suffix += 1
    return f"{base}-{suffix}"


def _experience_base(experience: Experience) -> str:
    base = f"exp-{slugify(experience.company)}"
    year = _YEAR.search(experience.period or "")
    return f"{base}-{year.group(0)}" if year else base


def assign_ids(profile: Profile) -> Profile:
    """Return a deep copy where every entity has a unique, stable id.

    Existing ids are preserved. Missing ones are derived from the content
    (``exp-acme-2022``) rather than from a random value, so re-importing a CV
    does not churn the ids the writer cites.
    """
    profile = profile.model_copy(deep=True)
    taken: set[str] = set()

    for experience in profile.experiences:
        if not experience.id or experience.id in taken:
            experience.id = _unique(_experience_base(experience), taken)
        taken.add(experience.id)

    for education in profile.education:
        if not education.id or education.id in taken:
            education.id = _unique(f"edu-{slugify(education.school)}", taken)
        taken.add(education.id)

    for project in profile.projects:
        if not project.id or project.id in taken:
            project.id = _unique(f"proj-{slugify(project.name)}", taken)
        taken.add(project.id)

    return profile

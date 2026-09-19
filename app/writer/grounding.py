"""Checking a draft against the profile.

The prompt states the rule — use only the profile, cite an achievement for every
claim. This module checks it, line by line, in code: a model that invents one
number is exactly the failure this application exists to avoid, and no prompt
makes that impossible.

The checks are deliberately lenient about wording and strict about facts: any
number must already be somewhere in the profile, every citation must exist, and a
skills line may not name something the candidate never listed. What counts as a
result, a skill or an entry title is decided from the model of the profile, so a
school or a project is a valid entry title just like a job.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from app.ats import normalize
from app.models import FACTUAL_ROLES, DraftLine, GroundingIssue, GroundingReport, Profile

# Keys dropped from the material: the ids themselves. Otherwise the digits of
# "exp-acme-2022-a1" would make almost every number look supported.
_ID_KEY = "id"
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
# A skill line is often written as the candidate's template writes it, with a
# category in front of the values ("Languages: Rust, Python"). The category is a
# label, not a claim, so only the values are checked.
_SKILL_SPLIT = re.compile(r"[;,]| — | – ")


def check(lines: list[DraftLine], profile: Profile) -> GroundingReport:
    material = _material(profile)
    known_ids = _achievement_ids(profile)
    entries = _entries(profile)
    profile_name = normalize(profile.identity.name)

    issues: list[GroundingIssue] = []
    for index, line in enumerate(lines):
        text = normalize(line.text)
        if not text:
            continue

        if line.role in FACTUAL_ROLES:
            if not line.achievement_ids:
                issues.append(
                    _issue(index, line, "states a result without citing one of your achievements")
                )
            unknown = [item for item in line.achievement_ids if item not in known_ids]
            if unknown:
                issues.append(
                    _issue(index, line, f"cites {', '.join(unknown)}, absent from your profile")
                )
            invented = [number for number in _NUMBER.findall(line.text) if number not in material]
            if invented:
                issues.append(
                    _issue(
                        index,
                        line,
                        f"states {', '.join(invented)}, which your profile does not contain",
                    )
                )
        elif line.role == "skill_line":
            for item in _skill_items(line.text):
                if item and normalize(item) not in material:
                    issues.append(
                        _issue(index, line, f"lists “{item}”, which is not in your profile")
                    )
        elif line.role == "entry_title" and entries:
            if not any(entry in text for entry in entries):
                issues.append(
                    _issue(index, line, "does not match any experience, school or project in your profile")
                )
        elif line.role == "name" and profile_name:
            if profile_name not in text:
                issues.append(_issue(index, line, "is not the name in your profile"))

    return GroundingReport(checked=len(lines), issues=issues)


def _issue(index: int, line: DraftLine, reason: str) -> GroundingIssue:
    return GroundingIssue(index=index, role=line.role, text=line.text, reason=reason)


def _material(profile: Profile) -> str:
    return normalize(" \n ".join(_flatten(profile.model_dump(exclude_defaults=True))))


def _flatten(value: object) -> Iterator[str]:
    """Every string and number the profile states, minus the ids."""
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, (int, float)):
        yield str(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key == _ID_KEY:
                continue
            yield from _flatten(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _flatten(item)


def _achievement_ids(profile: Profile) -> set[str]:
    return {
        achievement.id
        for experience in profile.experiences
        for achievement in experience.achievements
        if achievement.id
    }


def _entries(profile: Profile) -> set[str]:
    """Every entry title the profile justifies: a job, a school or a project."""
    names: set[str] = set()
    for experience in profile.experiences:
        for value in (experience.company, experience.title):
            if value.strip():
                names.add(normalize(value))
    for education in profile.education:
        for value in (education.school, education.degree):
            if value.strip():
                names.add(normalize(value))
    for project in profile.projects:
        if project.name.strip():
            names.add(normalize(project.name))
    return names


def _skill_items(text: str) -> list[str]:
    """The skills a line lists, the category label aside."""
    body = text.split(":", 1)[1] if ":" in text else text
    return [item.strip() for item in _SKILL_SPLIT.split(body)]

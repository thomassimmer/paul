"""Checking a draft against the profile.

The prompt states the rule — use only the profile, cite an experience for every
claim. This module checks it, line by line, in code: a model that invents one
number is exactly the failure this application exists to avoid, and no prompt
makes that impossible.

The checks are deliberately lenient about wording and strict about facts: any
number must already be somewhere in the profile, every citation must exist, and a
skills line may not name something the candidate never listed. A headline cites
nothing — it is not a result — but it is the line a recruiter reads first, so it is
held to the numbers rule all the same. What counts as a result, a skill or an entry
title is decided from the model of the profile, so a school or a project is a valid
entry title just like a job.
"""

from __future__ import annotations

import re

from app.ats import normalize, profile_material
from app.models import (
    FACTUAL_ROLES,
    NUMBER_CHECKED_ROLES,
    DraftLine,
    GroundingIssue,
    GroundingReport,
    Profile,
)

# A number a line states must already appear in the material, which the ids are
# dropped from: the digits of "exp-acme-2022" support nothing on their own.
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
# A skill line is often written as the candidate's template writes it, with a
# category in front of the values ("Languages: Rust, Python"). The category is a
# label, not a claim, so only the values are checked. The dashes below are the
# separators being matched, so the lookalike characters are the point.
_SKILL_SPLIT = re.compile(r"[;,]| — | – ")  # noqa: RUF001


def check(lines: list[DraftLine], profile: Profile) -> GroundingReport:
    material = profile_material(profile)
    known_ids = _source_ids(profile)
    entries = _entries(profile)
    profile_name = normalize(profile.identity.name)

    issues: list[GroundingIssue] = []
    for index, line in enumerate(lines):
        text = normalize(line.text)
        if not text:
            continue

        if line.role in FACTUAL_ROLES:
            if not line.source_ids:
                issues.append(
                    _issue(index, line, "states a result without citing one of your experiences")
                )
            unknown = [item for item in line.source_ids if item not in known_ids]
            if unknown:
                issues.append(
                    _issue(index, line, f"cites {', '.join(unknown)}, absent from your profile")
                )
        if line.role in NUMBER_CHECKED_ROLES:
            invented = [number for number in _NUMBER.findall(line.text) if number not in material]
            if invented:
                issues.append(
                    _issue(
                        index,
                        line,
                        f"states {', '.join(invented)}, which your profile does not contain",
                    )
                )
        if line.role == "skill_line":
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
        elif line.role == "name" and profile_name and profile_name not in text:
            issues.append(_issue(index, line, "is not the name in your profile"))

    return GroundingReport(checked=len(lines), issues=issues)


def _issue(index: int, line: DraftLine, reason: str) -> GroundingIssue:
    return GroundingIssue(index=index, role=line.role, text=line.text, reason=reason)


def _source_ids(profile: Profile) -> set[str]:
    """The entries a factual line may cite. An experience is the unit now that a
    result is a line of its ``highlights`` rather than a thing with its own id."""
    return {experience.id for experience in profile.experiences if experience.id}


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

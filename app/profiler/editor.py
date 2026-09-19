"""The structured profile editor.

Rendering and parsing stay here (away from HTTP and templates) so the form can
be unit-tested as a plain function. The editor always renders one blank row per
list, so adding an entry never needs JavaScript: a row is created by filling
the trailing blank one, and deleted by emptying it.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.models import (
    Achievement,
    Education,
    Experience,
    Identity,
    Profile,
    Project,
)
from app.profiler.ids import assign_ids
from app.profiler.text import extract_metrics, split_list


def parse_skills(text: str) -> dict[str, list[str]]:
    """Parse the skills textarea: one ``Group: item, item`` line per family."""
    groups: dict[str, list[str]] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        name, separator, rest = line.partition(":")
        group = name.strip() if separator else "Other"
        items = split_list(rest if separator else line)
        if items:
            groups.setdefault(group or "Other", []).extend(items)
    return groups


def format_skills(skills: Mapping[str, list[str]]) -> str:
    return "\n".join(
        f"{name}: {', '.join(items)}" for name, items in skills.items() if items
    )


def _text(form: Mapping[str, object], key: str, default: str = "") -> str:
    value = form.get(key)
    return default if value is None else str(value).strip()


def _number(form: Mapping[str, object], key: str, default: int = 0) -> int:
    try:
        return int(str(form.get(key, "")).strip())
    except (TypeError, ValueError):
        return default


def editor_view(profile: Profile) -> dict:
    """Rows for the template, each list ending with one blank row.

    Blank rows are how entries are added without JavaScript: filling the
    trailing row creates the entry, emptying a row deletes it.
    """
    achievement_index = 0
    experiences: list[dict] = []
    for position, experience in enumerate(profile.experiences):
        experiences.append(
            {
                "index": position,
                "experience": experience,
                "blank": False,
                "achievements": [
                    {"index": -1, "achievement": achievement}
                    for achievement in experience.achievements
                ],
            }
        )
    experiences.append(
        {
            "index": len(profile.experiences),
            "experience": Experience(),
            "blank": True,
            "achievements": [],
        }
    )

    for item in experiences:
        for row in item["achievements"]:
            row["index"] = achievement_index
            achievement_index += 1
        item["blank_index"] = achievement_index
        achievement_index += 1

    def rows(entries: list, factory) -> list[dict]:
        return [
            *({"index": i, "blank": False, "entry": entry} for i, entry in enumerate(entries)),
            {"index": len(entries), "blank": True, "entry": factory()},
        ]

    return {
        "experiences": experiences,
        "experience_count": len(experiences),
        "achievement_count": achievement_index,
        "education_rows": rows(profile.education, Education),
        "project_rows": rows(profile.projects, Project),
        "skills_text": format_skills(profile.skills),
    }


def profile_from_form(form: Mapping[str, object], current: Profile | None = None) -> Profile:
    """Build a profile from the editor form.

    Starts from ``current`` so fields the form does not show (hand-added facts,
    for instance) survive a save.
    """
    profile = (current or Profile()).model_copy(deep=True)

    profile.identity = Identity(
        name=_text(form, "identity.name"),
        headline=_text(form, "identity.headline"),
        location=_text(form, "identity.location"),
        email=_text(form, "identity.email"),
        phone=_text(form, "identity.phone"),
        links=split_list(_text(form, "identity.links")),
    )

    facts = profile.facts.model_copy(deep=True)
    for field in ("work_authorization", "notice_period", "salary_expectation", "relocation"):
        setattr(facts, field, _text(form, f"facts.{field}"))
    facts.languages = split_list(_text(form, "facts.languages"))
    profile.facts = facts

    preferences = profile.preferences.model_copy(deep=True)
    for field in ("target_roles", "locations", "contract_types", "more_of", "less_of"):
        setattr(preferences, field, split_list(_text(form, f"preferences.{field}")))
    preferences.remote = _text(form, "preferences.remote")
    profile.preferences = preferences

    profile.skills = parse_skills(_text(form, "skills"))
    profile.experiences = _experiences_from_form(form)
    profile.education = _education_from_form(form)
    profile.projects = _projects_from_form(form)

    return assign_ids(profile)


def _experiences_from_form(form: Mapping[str, object]) -> list[Experience]:
    by_index: dict[int, Experience] = {}
    for index in range(_number(form, "exp_count")):
        prefix = f"exp.{index}."
        company = _text(form, prefix + "company")
        title = _text(form, prefix + "title")
        if not company and not title:
            continue  # blank trailing row
        by_index[index] = Experience(
            id=_text(form, prefix + "id"),
            company=company,
            title=title,
            period=_text(form, prefix + "period"),
            context=_text(form, prefix + "context"),
            team_size=_text(form, prefix + "team_size"),
            stack=split_list(_text(form, prefix + "stack")),
            difficulties=_text(form, prefix + "difficulties"),
        )

    for index in range(_number(form, "ach_count")):
        prefix = f"ach.{index}."
        text = _text(form, prefix + "text")
        owner = by_index.get(_number(form, prefix + "exp_index", -1))
        if not text or owner is None:
            continue
        owner.achievements.append(
            Achievement(
                id=_text(form, prefix + "id"),
                text=text,
                metrics=split_list(_text(form, prefix + "metrics")) or extract_metrics(text),
                skills=split_list(_text(form, prefix + "skills")),
            )
        )

    return [by_index[index] for index in sorted(by_index)]


def _education_from_form(form: Mapping[str, object]) -> list[Education]:
    entries = []
    for index in range(_number(form, "edu_count")):
        prefix = f"edu.{index}."
        school = _text(form, prefix + "school")
        degree = _text(form, prefix + "degree")
        if not school and not degree:
            continue
        entries.append(
            Education(
                id=_text(form, prefix + "id"),
                school=school,
                degree=degree,
                period=_text(form, prefix + "period"),
                details=_text(form, prefix + "details"),
            )
        )
    return entries


def _projects_from_form(form: Mapping[str, object]) -> list[Project]:
    entries = []
    for index in range(_number(form, "proj_count")):
        prefix = f"proj.{index}."
        name = _text(form, prefix + "name")
        description = _text(form, prefix + "description")
        if not name and not description:
            continue
        entries.append(
            Project(
                id=_text(form, prefix + "id"),
                name=name,
                description=description,
                links=split_list(_text(form, prefix + "links")),
                skills=split_list(_text(form, prefix + "skills")),
            )
        )
    return entries

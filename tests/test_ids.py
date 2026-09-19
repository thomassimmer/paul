from __future__ import annotations

from app.models import Achievement, Experience, Profile, Project
from app.profiler.ids import assign_ids, slugify


def test_slugify():
    assert slugify("Acme Analytics") == "acme-analytics"
    assert slugify("  Éditeur & Cie  ") == "diteur-cie"
    assert slugify("!!!") == "item"


def test_assign_ids_derives_from_content():
    profile = assign_ids(
        Profile(
            experiences=[
                Experience(
                    company="Acme",
                    period="2022-03 / 2024-06",
                    achievements=[Achievement(text="Cut latency by 60%")],
                )
            ]
        )
    )
    assert profile.experiences[0].id == "exp-acme-2022"
    assert profile.experiences[0].achievements[0].id == "exp-acme-2022-a1"


def test_assign_ids_keeps_existing_ids():
    profile = assign_ids(
        Profile(experiences=[Experience(id="exp-kept", company="Acme", period="2022")])
    )
    assert profile.experiences[0].id == "exp-kept"


def test_assign_ids_does_not_mutate_the_input():
    original = Profile(experiences=[Experience(company="Acme", period="2022")])
    assign_ids(original)
    assert original.experiences[0].id == ""


def test_assign_ids_is_deterministic_and_unique():
    profile = Profile(
        experiences=[
            Experience(company="Acme", period="2022"),
            Experience(company="Acme", period="2022"),
        ]
    )
    first = assign_ids(profile)
    second = assign_ids(profile)
    ids = [experience.id for experience in first.experiences]
    assert ids == [experience.id for experience in second.experiences]
    assert len(set(ids)) == 2


def test_assign_ids_fills_blank_achievements_without_collision():
    profile = assign_ids(
        Profile(
            experiences=[
                Experience(
                    company="Acme",
                    period="2022",
                    achievements=[Achievement(id="exp-acme-2022-a1", text="kept")],
                )
            ]
        )
    )
    # A second, blank achievement must not reuse a1.
    profile.experiences[0].achievements.append(Achievement(text="new"))
    profile = assign_ids(profile)
    ids = [achievement.id for achievement in profile.experiences[0].achievements]
    assert ids == ["exp-acme-2022-a1", "exp-acme-2022-a2"]


def test_assign_ids_covers_education_and_projects():
    profile = assign_ids(
        Profile(
            education=[],
            projects=[Project(name="Job board scraper", description="Small tool")],
        )
    )
    assert profile.projects[0].id == "proj-job-board-scraper"

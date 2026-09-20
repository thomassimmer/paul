from __future__ import annotations

from app.models import Experience, Profile, Project
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
                    highlights=["Cut latency by 60%"],
                )
            ]
        )
    )
    assert profile.experiences[0].id == "exp-acme-2022"


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


def test_assign_ids_gives_colliding_experiences_a_unique_suffix():
    # Two experiences that derive the same base id must not end up sharing one:
    # the writer cites the id, so the second gets a numeric suffix.
    profile = assign_ids(
        Profile(
            experiences=[
                Experience(company="Acme", period="2022"),
                Experience(company="Acme", period="2022"),
            ]
        )
    )
    ids = [experience.id for experience in profile.experiences]
    assert ids == ["exp-acme-2022", "exp-acme-2022-2"]


def test_assign_ids_covers_education_and_projects():
    profile = assign_ids(
        Profile(
            education=[],
            projects=[Project(name="Job board scraper", description="Small tool")],
        )
    )
    assert profile.projects[0].id == "proj-job-board-scraper"

from __future__ import annotations

from app.models import Experience, Facts, Profile
from app.profiler import editor


def test_editor_view_ends_every_list_with_a_blank_row():
    profile = Profile(
        experiences=[
            Experience(id="exp-acme-2022", company="Acme", highlights=["Cut latency by 60%"])
        ],
        education=[],
        projects=[],
    )
    view = editor.editor_view(profile)

    assert [item["blank"] for item in view["experiences"]] == [False, True]
    assert view["experience_count"] == 2
    # The existing experience, then the blank row that adds the next one.
    assert view["experiences"][0]["index"] == 0
    assert view["experiences"][0]["experience"].id == "exp-acme-2022"
    assert view["experiences"][1]["index"] == 1
    assert [row["blank"] for row in view["education_rows"]] == [True]
    assert [row["blank"] for row in view["project_rows"]] == [True]


def _base_form(**overrides: str) -> dict[str, str]:
    form = {
        "identity.name": "Camille Moreau",
        "identity.links": "https://example.com, https://github.com/x",
        "facts.languages": "French, English",
        "exp_count": "0",
        "edu_count": "0",
        "proj_count": "0",
    }
    form.update(overrides)
    return form


def test_profile_from_form_sets_scalars_and_lists():
    profile = editor.profile_from_form(_base_form())
    assert profile.identity.name == "Camille Moreau"
    assert profile.identity.links == ["https://example.com", "https://github.com/x"]
    assert profile.facts.languages == ["French", "English"]


def test_profile_from_form_adds_an_experience_with_highlights():
    form = _base_form(
        exp_count="1",
        **{
            "exp.0.id": "",
            "exp.0.company": "Acme",
            "exp.0.title": "Lead Backend Engineer",
            "exp.0.period": "2022-03 / 2024-06",
            "exp.0.stack": "Rust, Kafka",
            # One highlight per line: a comma inside a sentence is kept.
            "exp.0.highlights": "Cut latency, from 900ms to 120ms\nLed the migration",
        },
    )
    profile = editor.profile_from_form(form)

    experience = profile.experiences[0]
    assert experience.id == "exp-acme-2022"
    assert experience.stack == ["Rust", "Kafka"]
    assert experience.highlights == [
        "Cut latency, from 900ms to 120ms",
        "Led the migration",
    ]


def test_profile_from_form_drops_emptied_rows():
    form = _base_form(
        exp_count="2",
        **{
            "exp.0.id": "exp-acme-2022",
            "exp.0.company": "Acme",
            "exp.0.title": "Engineer",
            "exp.0.highlights": "Kept",
            # exp.1 is the blank trailing row: left empty, so it disappears.
        },
    )
    profile = editor.profile_from_form(form)

    assert [experience.company for experience in profile.experiences] == ["Acme"]
    assert profile.experiences[0].highlights == ["Kept"]


def test_profile_from_form_keeps_hand_added_facts():
    current = Profile(
        facts=Facts.model_validate(
            {"work_authorization": "EU citizen", "driving_licence": "B"}
        )
    )

    profile = editor.profile_from_form(_base_form(), current)

    assert profile.facts.model_dump()["driving_licence"] == "B"
    assert profile.facts.work_authorization == ""  # the form shows this field


def test_profile_from_form_preserves_existing_ids():
    form = _base_form(
        exp_count="1",
        **{
            "exp.0.id": "exp-kept",
            "exp.0.company": "Acme",
            "exp.0.title": "Engineer",
        },
    )
    profile = editor.profile_from_form(form)
    assert profile.experiences[0].id == "exp-kept"


def test_profile_from_form_handles_missing_counts():
    profile = editor.profile_from_form({"identity.name": "Camille"})
    assert profile.identity.name == "Camille"
    assert profile.experiences == []

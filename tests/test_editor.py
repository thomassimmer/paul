from __future__ import annotations

from app.models import Achievement, Experience, Facts, Profile
from app.profiler import editor


def test_parse_and_format_skills_round_trip():
    text = "Languages: Rust, Python\nTools: Kafka, Docker"
    assert editor.parse_skills(text) == {
        "Languages": ["Rust", "Python"],
        "Tools": ["Kafka", "Docker"],
    }
    assert editor.format_skills(editor.parse_skills(text)) == text


def test_skills_without_a_group_go_to_other():
    assert editor.parse_skills("Rust, Python") == {"Other": ["Rust", "Python"]}


def test_editor_view_ends_every_list_with_a_blank_row():
    profile = Profile(
        experiences=[Experience(id="exp-acme-2022", company="Acme", achievements=[Achievement(id="exp-acme-2022-a1", text="x")])],
        education=[],
        projects=[],
    )
    view = editor.editor_view(profile)

    assert [item["blank"] for item in view["experiences"]] == [False, True]
    assert view["experience_count"] == 2
    # One row for the existing achievement, one blank per experience card.
    assert view["achievement_count"] == 3
    assert view["experiences"][0]["achievements"][0]["index"] == 0
    assert view["experiences"][0]["blank_index"] == 1
    assert view["experiences"][1]["blank_index"] == 2
    assert [row["blank"] for row in view["education_rows"]] == [True]
    assert [row["blank"] for row in view["project_rows"]] == [True]


def _base_form(**overrides: str) -> dict[str, str]:
    form = {
        "identity.name": "Camille Moreau",
        "identity.links": "https://example.com, https://github.com/x",
        "facts.languages": "French, English",
        "skills": "Languages: Rust",
        "exp_count": "0",
        "ach_count": "0",
        "edu_count": "0",
        "proj_count": "0",
    }
    form.update(overrides)
    return form


def test_profile_from_form_sets_scalars_lists_and_skills():
    profile = editor.profile_from_form(_base_form())
    assert profile.identity.name == "Camille Moreau"
    assert profile.identity.links == ["https://example.com", "https://github.com/x"]
    assert profile.facts.languages == ["French", "English"]
    assert profile.skills == {"Languages": ["Rust"]}


def test_profile_from_form_adds_an_experience_and_an_achievement():
    form = _base_form(
        exp_count="1",
        **{
            "exp.0.id": "",
            "exp.0.company": "Acme",
            "exp.0.title": "Lead Backend Engineer",
            "exp.0.period": "2022-03 / 2024-06",
            "exp.0.stack": "Rust, Kafka",
            "ach_count": "1",
            "ach.0.exp_index": "0",
            "ach.0.text": "Cut ingestion latency by 60%",
            "ach.0.metrics": "",
        },
    )
    profile = editor.profile_from_form(form)

    experience = profile.experiences[0]
    assert experience.id == "exp-acme-2022"
    assert experience.stack == ["Rust", "Kafka"]
    assert experience.achievements[0].id == "exp-acme-2022-a1"
    # Metrics are derived from the text when the field is left empty.
    assert experience.achievements[0].metrics == ["60%"]


def test_profile_from_form_drops_emptied_rows():
    form = _base_form(
        exp_count="2",
        ach_count="2",
        **{
            "exp.0.id": "exp-acme-2022",
            "exp.0.company": "Acme",
            "exp.0.title": "Engineer",
            # exp.1 is the blank trailing row: left empty, so it disappears.
            "ach.0.exp_index": "0",
            "ach.0.text": "Kept",
            "ach.1.exp_index": "1",
            "ach.1.text": "Attached to a dropped experience",
        },
    )
    profile = editor.profile_from_form(form)

    assert [experience.company for experience in profile.experiences] == ["Acme"]
    assert [a.text for a in profile.experiences[0].achievements] == ["Kept"]


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
        ach_count="1",
        **{
            "exp.0.id": "exp-kept",
            "exp.0.company": "Acme",
            "exp.0.title": "Engineer",
            "ach.0.id": "exp-kept-a9",
            "ach.0.exp_index": "0",
            "ach.0.text": "Kept achievement",
        },
    )
    profile = editor.profile_from_form(form)
    assert profile.experiences[0].id == "exp-kept"
    assert profile.experiences[0].achievements[0].id == "exp-kept-a9"


def test_profile_from_form_handles_missing_counts():
    profile = editor.profile_from_form({"identity.name": "Camille"})
    assert profile.identity.name == "Camille"
    assert profile.experiences == []

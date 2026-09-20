from __future__ import annotations

import pytest
import yaml

from app.models import Experience, Facts, Identity, Profile
from app.profiler import store


def test_load_returns_none_before_first_import():
    assert store.load_profile() is None


def test_save_then_load_round_trip():
    saved = store.save_profile(
        Profile(
            identity=Identity(name="Camille"),
            experiences=[Experience(company="Acme", title="Engineer", period="2022")],
        )
    )
    loaded = store.load_profile()
    assert loaded is not None
    assert loaded.identity.name == "Camille"
    assert loaded.experiences[0].id == saved.experiences[0].id


def test_file_is_readable_yaml_with_a_header():
    store.save_profile(Profile(experiences=[Experience(company="Acme", period="2022")]))
    text = store.PROFILE_PATH.read_text(encoding="utf-8")
    assert text.startswith("# Paul profile")
    parsed = yaml.safe_load(text)
    assert parsed["experiences"][0]["id"] == "exp-acme-2022"


def test_defaults_are_left_out_of_the_file():
    store.save_profile(Profile())
    text = store.PROFILE_PATH.read_text(encoding="utf-8")
    assert "facts" not in text
    assert "experiences" not in text


def test_hand_added_facts_survive_a_save():
    store.save_profile(Profile(facts=Facts(work_authorization="EU citizen")))
    # Simulate a hand edit: add a key the model does not know about.
    raw = yaml.safe_load(store.PROFILE_PATH.read_text(encoding="utf-8"))
    raw["facts"]["driving_licence"] = "B"
    store.PROFILE_PATH.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    profile = store.load_profile()
    assert profile is not None
    assert profile.facts.model_dump()["driving_licence"] == "B"

    store.save_profile(profile)
    assert "driving_licence" in store.PROFILE_PATH.read_text(encoding="utf-8")


def test_broken_yaml_is_reported_not_raised_as_yaml_error():
    store.PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    store.PROFILE_PATH.write_text("experiences: [oops\n", encoding="utf-8")
    with pytest.raises(store.ProfileError):
        store.load_profile()


def test_unknown_shape_is_reported():
    store.PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    store.PROFILE_PATH.write_text("experiences: 42\n", encoding="utf-8")
    with pytest.raises(store.ProfileError):
        store.load_profile()


def test_cv_text_is_kept_for_re_runs():
    assert store.load_cv_text() is None
    store.save_cv_text("Camille Moreau")
    assert store.load_cv_text() == "Camille Moreau"


def test_a_file_written_before_highlights_keeps_what_it_recorded():
    """An older profile folds its achievements and difficulties into highlights.

    Nothing it recorded is dropped — including what the sentence alone did not
    say: the figures an achievement measured, and the technologies it named.
    """
    store.PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    legacy = {
        "experiences": [
            {
                "id": "exp-acme-2022",
                "company": "Acme",
                "context": "B2B invoicing SaaS",
                "stack": ["Rust"],
                "difficulties": "Rebuilding without downtime",
                "achievements": [
                    {
                        "id": "exp-acme-2022-a1",
                        "text": "Led the migration",
                        "metrics": ["13M invoices", "10 hours"],
                        "skills": ["Kafka", "Rust"],
                    },
                    {"text": "Led 40 services"},
                ],
            }
        ]
    }
    store.PROFILE_PATH.write_text(yaml.safe_dump(legacy, sort_keys=False), encoding="utf-8")

    profile = store.load_profile()
    assert profile is not None
    experience = profile.experiences[0]
    assert experience.highlights == [
        "Led the migration (13M invoices, 10 hours)",
        "Led 40 services",
    ]
    # The technologies an achievement named join the stack of the job, without
    # duplicating what is already there.
    assert experience.stack == ["Rust", "Kafka"]
    # What was hard about the job is context, not a bullet.
    assert experience.context == "B2B invoicing SaaS\nRebuilding without downtime"

    store.save_profile(profile)
    text = store.PROFILE_PATH.read_text(encoding="utf-8")
    assert "achievements" not in text and "difficulties" not in text

    saved = store.load_profile()
    assert saved is not None
    assert saved.experiences[0].highlights[0] == "Led the migration (13M invoices, 10 hours)"


def test_interview_questions_asked_are_stored_in_sqlite():
    assert store.asked_questions() == []
    store.remember_question("What did you build there?")
    store.remember_question("What did you build there?")  # idempotent
    store.remember_question("  What   did you build there?  ")  # whitespace collapsed
    assert store.asked_questions() == ["What did you build there?"]

    store.remember_question("Why did you leave?")
    assert store.asked_questions() == [
        "What did you build there?",
        "Why did you leave?",
    ]

    store.forget_questions()
    assert store.asked_questions() == []

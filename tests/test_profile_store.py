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


def test_interview_skips_are_stored_in_sqlite():
    assert store.skipped_keys() == set()
    store.skip_key("facts:notice_period")
    store.skip_key("facts:notice_period")  # idempotent
    assert store.skipped_keys() == {"facts:notice_period"}
    store.clear_skips()
    assert store.skipped_keys() == set()

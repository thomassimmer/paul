from __future__ import annotations

from pathlib import Path

import yaml

from app import models
from app.models import Profile

EXAMPLE = Path(models.__file__).parent / "examples" / "profile.example.yaml"


def test_example_profile_is_valid():
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    profile = Profile.model_validate(data)
    assert profile.identity.name
    assert profile.experiences
    assert profile.facts.work_authorization


def test_example_profile_ids_are_complete_and_stable():
    from app.profiler.ids import assign_ids

    profile = assign_ids(Profile.model_validate(yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))))
    ids = [
        entry.id
        for entry in [*profile.experiences, *profile.education, *profile.projects]
    ]
    assert all(ids)
    assert len(set(ids)) == len(ids)
    # The writer cites an experience, so each one carries its own id.
    assert all(experience.id.startswith("exp-") for experience in profile.experiences)

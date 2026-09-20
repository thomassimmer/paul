"""The demo seed has to build a workspace the app itself considers coherent.

The interesting assertions are the ones that would catch a fixture drifting away
from the profile: a verdict whose fingerprint no longer matches would show up as
"out of date" on the board, and a document that quotes a number the profile does
not contain would be flagged as unverified in the review screen.
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

SEED_PATH = Path(__file__).resolve().parent.parent / "scripts" / "seed_demo.py"
TODAY = date(2026, 9, 20)


def _seed_module():
    """``scripts/`` is not a package, so the module is loaded by path."""
    spec = importlib.util.spec_from_file_location("seed_demo", SEED_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seed_writes_a_workspace_the_app_reads_back():
    from app.config import load_settings
    from app.offers import store as offers_store
    from app.profiler import store as profile_store
    from app.ranking import service as ranking_service
    from app.ranking import store as ranking_store
    from app.tracker import service as tracker_service
    from app.tracker import store as tracker_store

    report = _seed_module().seed(today=TODAY)

    profile = profile_store.load_profile()
    assert profile is not None
    assert profile.identity.name == "Camille Moreau"
    assert profile_store.load_cv_text()

    settings = load_settings()
    assert settings.filter_rules and settings.wishes

    records = offers_store.list_offers()
    assert len(records) == 5
    # The board lists by descending id: the best offer is written last, shown first.
    assert records[0].offer.company == "Sentinel Health"

    for record in records:
        ranking = ranking_store.load_ranking(record.id)
        assert ranking is not None, f"{record.offer.company} has no verdict"
        assert not ranking_service.is_stale(settings, profile, record.offer, ranking)

    # A score is the sum of its axes: the seed cannot make it say anything else.
    top = records[0]
    top_ranking = ranking_store.load_ranking(top.id)
    assert top_ranking is not None and top_ranking.score is not None
    assert top_ranking.score.total == 90

    eliminated = [record for record in records if record.offer.company == "Orion Defence"]
    verdict = ranking_store.load_ranking(eliminated[0].id)
    assert verdict is not None
    assert verdict.eliminated
    assert verdict.elimination.excerpt and verdict.elimination.rule

    assert report["prepared"]

    applications = tracker_store.list_applications()
    cobalt = next(record for record in records if record.offer.company == "Cobalt Pay")
    due, days = tracker_service.followup(applications[cobalt.id], settings.followup_days, TODAY)
    assert due and days == 12

    ferrite = next(record for record in records if record.offer.company == "Ferrite Labs")
    due, _ = tracker_service.followup(applications[ferrite.id], settings.followup_days, TODAY)
    assert not due


def test_seeded_documents_survive_the_grounding_check():
    from app.offers import store as offers_store
    from app.profiler import store as profile_store
    from app.writer import service as writer_service

    report = _seed_module().seed(today=TODAY)
    profile = profile_store.load_profile()
    assert profile is not None
    record = next(
        item for item in offers_store.list_offers() if item.offer.company == "Sentinel Health"
    )

    review = writer_service.load_review(profile, record, report["prepared"])

    assert review.cv_grounding.ok, [issue.reason for issue in review.cv_grounding.issues]
    assert review.letter_grounding.ok, [issue.reason for issue in review.letter_grounding.issues]
    assert review.ats is not None and review.ats.coverage_percent > 0
    assert review.cv_text and review.letter_text and review.answers_text
    assert "cv.docx" in review.files and "letter.docx" in review.files


def test_seeding_twice_rebuilds_instead_of_accumulating():
    from app.offers import store as offers_store

    seed = _seed_module().seed
    seed(today=TODAY)
    seed(today=TODAY)

    assert len(offers_store.list_offers()) == 5


def test_reseeding_keeps_the_provider_settings():
    """Recording a second take must not mean pasting the API key again."""
    from app.config import load_settings, save_settings

    seed = _seed_module().seed
    seed(model="anthropic/claude-sonnet-4-5")
    save_settings(load_settings().model_copy(update={"api_key": "sk-not-a-real-key"}))

    seed()

    settings = load_settings()
    assert settings.api_key == "sk-not-a-real-key"
    assert settings.model == "anthropic/claude-sonnet-4-5"


def test_reseeding_keeps_an_imported_template():
    """A template is the user's own file, not demo data."""
    from app.templates_engine import default, store

    seed = _seed_module().seed
    seed()
    docx, blueprint = default.build("cv")
    store.save_custom("cv", docx, blueprint)

    seed()

    assert store.custom_path("cv").read_bytes() == docx
    assert store.custom_path("cv").exists() and store.blueprint_path("cv").exists()


def test_a_directory_this_script_did_not_create_is_refused():
    from app.config import DATA_DIR

    seed_module = _seed_module()
    Path(DATA_DIR, "profile").mkdir(parents=True, exist_ok=True)
    Path(DATA_DIR, "profile", "profile.yaml").write_text("identity: {}\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        seed_module.seed(today=TODAY)

    # Nothing was touched on the way out.
    assert Path(DATA_DIR, "profile", "profile.yaml").exists()

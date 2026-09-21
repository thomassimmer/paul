from __future__ import annotations

import asyncio

import pytest

from app import prompts
from app.config import Settings
from app.models import OfferDraft, Profile
from app.ranking import service


def test_every_packaged_prompt_loads():
    names = prompts.names()
    assert names, "no prompt found"
    assert "ranking/score.md" in names
    for name in names:
        assert prompts.load_prompt(name).strip()


def test_load_prompt_returns_the_default_when_nothing_is_overridden():
    assert prompts.load_prompt("ranking/eliminate.md") == prompts.default("ranking/eliminate.md")
    assert prompts.is_overridden("ranking/eliminate.md") is False


def test_an_override_takes_precedence_over_the_default():
    prompts.save_override("writer/write_letter.md", "Write the letter in two lines.")

    assert prompts.is_overridden("writer/write_letter.md") is True
    assert prompts.load_prompt("writer/write_letter.md") == "Write the letter in two lines."
    # The packaged file is untouched, so resetting later brings it back.
    assert "cover letter" in prompts.default("writer/write_letter.md").lower()


def test_reset_brings_the_default_back():
    original = prompts.load_prompt("ranking/score.md")
    prompts.save_override("ranking/score.md", "Score everything 5.")

    prompts.reset("ranking/score.md")

    assert prompts.is_overridden("ranking/score.md") is False
    assert prompts.load_prompt("ranking/score.md") == original


def test_an_unknown_name_is_refused():
    with pytest.raises(prompts.PromptError):
        prompts.load_prompt("../../app/config.py")
    with pytest.raises(prompts.PromptError):
        prompts.save_override("/etc/passwd", "nope")
    with pytest.raises(prompts.PromptError):
        prompts.reset("not/a/prompt.md")


def test_an_empty_override_is_refused_and_writes_nothing():
    with pytest.raises(prompts.PromptError):
        prompts.save_override("ranking/score.md", "   \n  ")

    assert prompts.is_overridden("ranking/score.md") is False


def test_the_digest_follows_the_text_in_use():
    before = prompts.digest(("ranking/score.md",))
    prompts.save_override("ranking/score.md", "Score everything 5.")
    assert prompts.digest(("ranking/score.md",)) != before


def test_editing_a_ranking_prompt_marks_the_scores_out_of_date():
    settings = Settings(model="openai/gpt-4o")
    offer = OfferDraft(title="An offer", company="Acme").to_offer([])
    before = service.fingerprint(settings, Profile(), offer)

    prompts.save_override("ranking/score.md", "Score everything 5.")

    assert service.fingerprint(settings, Profile(), offer) != before


def test_the_settings_page_shows_every_prompt(client):
    page = client.get("/settings").text

    assert 'id="prompts"' in page
    # Every prompt is editable inline, once.
    assert page.count('name="text"') == len(prompts.names())
    for name in prompts.names():
        assert name in page
    assert "Cover letter" in page
    assert "Offer scoring" in page
    assert "Read an offer" in page


def test_any_prompt_can_be_saved_from_the_settings_page(client):
    # Not just the taste-driven ones: a contract-heavy prompt is editable too.
    response = client.post(
        "/settings/prompts",
        data={"name": "offers/extract_offer.md", "text": "Extract what you can."},
    )

    assert response.status_code == 200
    assert prompts.load_prompt("offers/extract_offer.md") == "Extract what you can."


def test_saving_a_prompt_from_the_settings_page(client):
    response = client.post(
        "/settings/prompts",
        data={"name": "writer/form_answers.md", "text": "Answer in one sentence."},
    )

    assert response.status_code == 200  # the redirect is followed
    assert prompts.load_prompt("writer/form_answers.md") == "Answer in one sentence."
    assert "customized" in client.get("/settings").text


def test_resetting_a_prompt_from_the_settings_page(client):
    client.post("/settings/prompts", data={"name": "ranking/score.md", "text": "Score 5."})

    client.post("/settings/prompts", data={"name": "ranking/score.md", "action": "reset"})

    assert prompts.is_overridden("ranking/score.md") is False


def test_an_unknown_prompt_post_is_refused(client):
    response = client.post(
        "/settings/prompts",
        data={"name": "../config.py", "text": "nope"},
    )

    assert response.status_code == 200
    assert "Unknown prompt" in response.text


def test_an_empty_prompt_post_is_refused(client):
    response = client.post("/settings/prompts", data={"name": "ranking/score.md", "text": "  "})

    assert response.status_code == 200
    assert "cannot be empty" in response.text
    assert prompts.is_overridden("ranking/score.md") is False


def test_the_prompts_used_by_the_llm_are_the_ones_loaded(monkeypatch):
    """The override is what a caller actually sends, not just what the page shows."""
    from app.ranking import eliminate as eliminate_module

    sent: dict[str, str] = {}

    async def fake(settings, *, schema, content, system=None, retries=1, timeout=180.0):
        sent["system"] = system or ""
        return schema()

    monkeypatch.setattr(eliminate_module, "complete_structured", fake)
    prompts.save_override("ranking/eliminate.md", "Eliminate everything.")

    offer = OfferDraft(title="An offer", company="Acme").to_offer([])
    asyncio.run(
        eliminate_module.eliminate_offer(
            Settings(), offer=offer, profile=Profile(), rules="Eliminate clearance roles."
        )
    )

    assert sent["system"] == "Eliminate everything."

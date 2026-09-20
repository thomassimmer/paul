from __future__ import annotations

import asyncio

import pytest

from app.config import Settings
from app.llm import LLMError
from app.models import OfferDraft, Requirements
from app.offers import clean, service, store

FRAGMENT = """
<div>
  <h1>Senior Backend Engineer</h1>
  <p>Acme, Lyon. We are looking for a backend engineer to own the ingestion path.</p>
  <h2>Requirements</h2>
  <ul><li>5+ years with Rust</li><li>Kafka experience</li></ul>
  <form>
    <label for="why">Why do you want to join us?</label>
    <textarea id="why" name="why" maxlength="500" required></textarea>
  </form>
</div>
"""

MODEL = Settings(model="openai/gpt-4o")
URL = "https://example.com/jobs/42"


def _draft(**overrides) -> OfferDraft:
    base = {
        "title": "Senior Backend Engineer",
        "company": "Acme",
        "location": "Lyon",
        "contract_type": "Permanent",
        "seniority": "senior",
        "responsibilities": ["Own the ingestion path"],
        "requirements": Requirements(must_have=["Rust", "Kafka"]),
    }
    base.update(overrides)
    return OfferDraft(**base)


def _patch_extract(monkeypatch, draft: OfferDraft | None = None, error: Exception | None = None):
    calls: list[str] = []

    async def fake(settings, cleaned_text):
        calls.append(cleaned_text)
        if error is not None:
            raise error
        return draft if draft is not None else _draft()

    monkeypatch.setattr("app.offers.extract.extract_offer", fake)
    return calls


def test_analyze_stores_the_offer_with_the_parsed_form(monkeypatch):
    calls = _patch_extract(monkeypatch)
    outcome = asyncio.run(service.analyze(MODEL, [FRAGMENT], url=URL))

    assert outcome.level == "ok"
    assert outcome.record.id > 0
    assert outcome.record.offer.title == "Senior Backend Engineer"
    # The form came from the markup, not from the model.
    assert [q.name for q in outcome.record.offer.form] == ["why"]
    assert outcome.record.source == "html"
    # The link given at import is kept with the offer.
    assert outcome.record.url == URL
    # The model only ever sees the cleaned text.
    assert calls and "Why do you want to join us?" not in calls[0]
    assert store.load_offer(outcome.record.id) is not None


def test_analyze_warns_about_what_could_not_be_read(monkeypatch):
    _patch_extract(monkeypatch, _draft(company="", location="", responsibilities=[]))
    outcome = asyncio.run(service.analyze(MODEL, [FRAGMENT], url=URL))

    assert outcome.level == "warning"
    assert "could not be read" in outcome.notice
    assert "the company" in outcome.notice
    assert "the responsibilities" in outcome.notice


def test_analyze_warns_when_no_form_was_pasted(monkeypatch):
    _patch_extract(monkeypatch)
    outcome = asyncio.run(
        service.analyze(MODEL, ["<div><p>A description with no form in it at all.</p></div>"], url=URL)
    )
    assert "no application form" in outcome.notice


def test_analyze_merges_several_fragments(monkeypatch):
    calls = _patch_extract(monkeypatch)
    description = "<div><p>Acme is hiring a backend engineer for its ingestion team.</p></div>"
    form = '<form><label for="q">Why us?</label><input id="q" name="q"></form>'

    outcome = asyncio.run(service.analyze(MODEL, [description, form], url=URL))

    assert outcome.record.source == "html"
    assert [q.name for q in outcome.record.offer.form] == ["q"]
    assert outcome.record.id > 0
    assert calls


def test_analyze_needs_something_pasted():
    with pytest.raises(clean.CleanError):
        asyncio.run(service.analyze(MODEL, ["   ", ""], url=URL))


def test_analyze_needs_a_model(monkeypatch):
    _patch_extract(monkeypatch)
    with pytest.raises(LLMError, match="No model configured"):
        asyncio.run(service.analyze(Settings(), [FRAGMENT], url=URL))


def test_analyze_reports_a_provider_failure(monkeypatch):
    _patch_extract(monkeypatch, error=LLMError("provider is down"))
    with pytest.raises(LLMError, match="provider is down"):
        asyncio.run(service.analyze(MODEL, [FRAGMENT], url=URL))


def test_reanalyze_keeps_the_form_and_updates_the_rest(monkeypatch):
    _patch_extract(monkeypatch)
    outcome = asyncio.run(service.analyze(MODEL, [FRAGMENT], url=URL))
    offer_id = outcome.record.id

    _patch_extract(monkeypatch, _draft(title="Backend Engineer (Rust)", company="Acme"))
    again = asyncio.run(service.reanalyze(MODEL, outcome.record))

    assert again.record.id == offer_id
    assert again.record.offer.title == "Backend Engineer (Rust)"
    # Parsed by code, so it survives a re-analysis untouched.
    assert [q.name for q in again.record.offer.form] == ["why"]


def test_reanalyze_needs_a_model(monkeypatch):
    _patch_extract(monkeypatch)
    outcome = asyncio.run(service.analyze(MODEL, [FRAGMENT], url=URL))
    with pytest.raises(LLMError, match="No model configured"):
        asyncio.run(service.reanalyze(Settings(), outcome.record))


def test_missing_fields_lists_human_labels():
    assert "the company" in service.missing_fields(_draft(company=""))
    assert service.missing_fields(_draft()) == []

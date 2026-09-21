"""Offer analysis orchestration, kept out of the HTTP layer."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.llm import LLMError
from app.models import OfferDraft, OfferRecord
from app.offers import clean, extract, store

# Fields worth completing by hand when the offer text did not state them.
IMPORTANT_FIELDS = {
    "title": "the title",
    "company": "the company",
    "location": "the location",
    "contract_type": "the contract type",
    "seniority": "the seniority",
}


@dataclass
class AnalysisOutcome:
    record: OfferRecord
    notice: str = ""
    level: str = "ok"


def missing_fields(offer: OfferDraft) -> list[str]:
    """Human labels for what the model could not read from the offer.

    Takes an ``OfferDraft``: the check only looks at fields the model fills.
    """
    missing = [
        label
        for field, label in IMPORTANT_FIELDS.items()
        if not str(getattr(offer, field) or "").strip()
    ]
    if not offer.responsibilities:
        missing.append("the responsibilities")
    if not offer.requirements.must_have:
        missing.append("the must-have requirements")
    return missing


def _warnings(cleaned: clean.CleanedOffer, offer: OfferDraft) -> list[str]:
    """What could not be read, spelling it out for the notice."""
    warnings: list[str] = []
    if cleaned.truncated:
        warnings.append(f"the fragment was cut at {clean.MAX_CHARS} characters")
    if cleaned.form_fields == 0:
        warnings.append("no application form was found in the pasted fragment")
    missing = missing_fields(offer)
    if missing:
        warnings.append("could not be read: " + ", ".join(missing))
    return warnings


def prepare_fragment(settings: Settings, fragment: str) -> clean.CleanedOffer:
    """The local half of an analysis: clean the pasted fragment.

    Raises ``clean.CleanError`` when nothing usable was pasted and ``LLMError``
    when no model is configured: both are cheap and actionable, so the page
    answers them before dispatching the model call.
    """
    if not (fragment or "").strip():
        raise clean.CleanError("Paste the offer's HTML fragment or its text first.")
    if not settings.model.strip():
        raise LLMError("No model configured. Set one in Settings to analyze an offer.")
    return clean.clean_fragment(fragment)


async def extract_and_store(
    settings: Settings, raw: str, cleaned: clean.CleanedOffer, *, url: str
) -> AnalysisOutcome:
    """The model half: extract the offer from the cleaned text and store it."""
    draft = await extract.extract_offer(settings, cleaned.text)
    offer = draft.to_offer(cleaned.form)

    warnings = _warnings(cleaned, offer)
    record = store.save_offer(
        offer,
        raw=raw,
        cleaned=cleaned.text,
        source="html" if cleaned.html else "text",
        url=url,
    )
    notice = "Offer analyzed."
    if warnings:
        notice += " Warning — " + "; ".join(warnings) + "."
    return AnalysisOutcome(record=record, notice=notice, level="warning" if warnings else "ok")


async def analyze(settings: Settings, fragment: str, *, url: str) -> AnalysisOutcome:
    """Clean the pasted fragment, extract the offer, and store everything.

    Raises ``clean.CleanError`` when nothing usable was pasted and ``LLMError``
    when no model is configured or the provider fails.
    """
    cleaned = prepare_fragment(settings, fragment)
    return await extract_and_store(settings, fragment, cleaned, url=url)


def prepare_reanalysis(settings: Settings, record: OfferRecord) -> str:
    """The local half of a re-analysis: find the cleaned text to run again."""
    if not settings.model.strip():
        raise LLMError("No model configured. Set one in Settings to analyze an offer.")
    source = store.load_source(record.id)
    if source is None:
        raise LLMError("This offer is no longer stored.")
    _, cleaned_text = source
    if not cleaned_text.strip():
        raise LLMError("The cleaned text of this offer was not kept; analyze it again.")
    return cleaned_text


async def update_from_model(
    settings: Settings, record: OfferRecord, cleaned_text: str
) -> AnalysisOutcome:
    """The model half: re-extract and update the offer, keeping its form.

    The application form was parsed from the markup, not by the model, so it is
    carried over untouched.
    """
    draft = await extract.extract_offer(settings, cleaned_text)
    store.update_offer(record.id, draft.to_offer(record.offer.form))
    updated = store.load_offer(record.id)
    if updated is None:  # pragma: no cover - deleted between the two statements
        raise LLMError("This offer is no longer stored.")
    return AnalysisOutcome(record=updated, notice="Offer analyzed again.")


async def reanalyze(settings: Settings, record: OfferRecord) -> AnalysisOutcome:
    """Re-run the extraction from the stored cleaned text."""
    cleaned_text = prepare_reanalysis(settings, record)
    return await update_from_model(settings, record, cleaned_text)

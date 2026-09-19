"""LLM step: turn cleaned offer text into a structured draft."""

from __future__ import annotations

from app.config import Settings
from app.llm import complete_structured
from app.models import Keyword, OfferDraft
from app.prompts import load_prompt


def clean_keywords(keywords: list[Keyword]) -> list[Keyword]:
    """Drop noise: a variant identical to its term is not a variant.

    Models tend to echo the term (``Rust -> [Rust]``), which would later pollute
    the keyword coverage check. Cheaper and more reliable to fix it here than to
    insist in the prompt.
    """
    cleaned: list[Keyword] = []
    seen: set[str] = set()
    for keyword in keywords:
        term = keyword.term.strip()
        if not term or term.casefold() in seen:
            continue
        seen.add(term.casefold())
        variants = [
            variant.strip()
            for variant in keyword.variants
            if variant.strip() and variant.strip().casefold() != term.casefold()
        ]
        cleaned.append(Keyword(term=term, variants=variants))
    return cleaned


async def extract_offer(settings: Settings, cleaned_text: str) -> OfferDraft:
    """Return the offer as the model reads it.

    The application form is not asked for here: ``clean.py`` already parsed it
    from the markup, which is more faithful and cheaper.
    """
    draft = await complete_structured(
        settings,
        schema=OfferDraft,
        content=cleaned_text,
        system=load_prompt("offers/extract_offer.md"),
    )
    draft.keywords = clean_keywords(draft.keywords)
    return draft

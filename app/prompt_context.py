"""How a profile, an offer and the wishes are shown to the prompts.

Shared by the ranker and the writer so both describe the candidate the same way.
YAML rather than JSON: it reads like the profile file the user already knows, and
it costs fewer tokens for the same information.
"""

from __future__ import annotations

import yaml

from app.config import Settings, Wish
from app.models import Offer, Profile

# LiteLLM models answer with a two-letter code when asked for a language, but a
# pasted offer sometimes gets one of these names instead.
LANGUAGE_NAMES = {
    "en": "English",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "it": "Italian",
    "nl": "Dutch",
    "pt": "Portuguese",
}
_BY_NAME = {name.casefold(): code for code, name in LANGUAGE_NAMES.items()}


def profile_text(profile: Profile) -> str:
    """The profile as the user owns it, minus the empty fields."""
    return yaml.safe_dump(
        profile.model_dump(exclude_defaults=True),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=100,
    ).strip()


def offer_text(offer: Offer) -> str:
    """The extracted offer. The application form is left out: it is not criteria."""
    payload = offer.model_dump(mode="json", exclude_defaults=True, exclude={"form"})
    return yaml.safe_dump(
        payload, allow_unicode=True, sort_keys=False, default_flow_style=False, width=100
    ).strip()


def wishes_text(wishes: list[Wish]) -> str:
    """One ``label (weight N)`` line per wish, or a note when there are none."""
    lines = [f"- {wish.label} (weight {wish.weight:g})" for wish in wishes if wish.label.strip()]
    return "\n".join(lines) if lines else "(none given)"


def language_code(settings: Settings, offer: Offer) -> str:
    """The language the writer must produce, as a two-letter code.

    ``auto`` follows the offer; an unknown or missing offer language falls back to
    English rather than to the interface language, because the recruiter is the
    one who will read the document.
    """
    if settings.output_language != "auto" and settings.output_language in LANGUAGE_NAMES:
        return settings.output_language
    raw = (offer.language or "").strip().casefold()
    if raw in LANGUAGE_NAMES:
        return raw
    if raw in _BY_NAME:
        return _BY_NAME[raw]
    code = raw[:2]
    return code if code in LANGUAGE_NAMES else "en"


def output_language_text(settings: Settings, offer: Offer) -> str:
    """One line telling the writer which language to write in."""
    code = language_code(settings, offer)
    if settings.output_language != "auto" and settings.output_language in LANGUAGE_NAMES:
        return f"Write in {LANGUAGE_NAMES[code]}, whatever the offer's language."
    return f"Write in {LANGUAGE_NAMES[code]}, the language of the offer."

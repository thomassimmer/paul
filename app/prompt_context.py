"""How a profile, an offer and the wishes are shown to the ranking prompts.

YAML rather than JSON: it reads like the profile file the user already knows, and
it costs fewer tokens for the same information.
"""

from __future__ import annotations

import yaml

from app.config import Wish
from app.models import Offer, Profile


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

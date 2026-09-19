"""Stage 1: elimination.

An offer is eliminated only against a rule the candidate wrote, and only with the
passage that triggers it quoted back. An elimination without evidence is
downgraded here, in code, so nothing ever disappears silently.
"""

from __future__ import annotations

from app.config import Settings
from app.llm import complete_structured
from app.models import Elimination, Offer, Profile
from app.prompts import load_prompt
from app.prompt_context import offer_text, profile_text


def build_elimination(decision: Elimination) -> Elimination:
    """Keep an elimination only when it names the rule and quotes the offer."""
    if not decision.eliminated:
        return Elimination()
    rule = decision.rule.strip()
    excerpt = decision.excerpt.strip()
    if not rule or not excerpt:
        return Elimination()
    return Elimination(eliminated=True, rule=rule, excerpt=excerpt)


def content(*, offer: Offer, profile: Profile, rules: str) -> str:
    return (
        "## Candidate's elimination rules\n"
        f"{rules.strip()}\n\n"
        "## Candidate profile\n"
        f"{profile_text(profile)}\n\n"
        "## Offer to evaluate\n"
        f"{offer_text(offer)}\n"
    )


async def eliminate_offer(
    settings: Settings, *, offer: Offer, profile: Profile, rules: str
) -> Elimination:
    """Apply the candidate's rules to one offer.

    No rules means no elimination and no model call: there is nothing to apply.
    """
    if not rules.strip():
        return Elimination()
    decision = await complete_structured(
        settings,
        schema=Elimination,
        content=content(offer=offer, profile=profile, rules=rules),
        system=load_prompt("ranking/eliminate.md"),
    )
    return build_elimination(decision)

"""Stage 2: the scoring grid.

The model fills the four axes; the weights and the total are ours. That is what
makes a score explainable (every axis carries its own justification) and stable
(the same grid always gives the same total, whatever the model says about it).
"""

from __future__ import annotations

from app.config import Settings, Wish
from app.llm import complete_structured
from app.models import AxisScore, Offer, Profile, Score, ScoringGrid
from app.prompts import load_prompt
from app.prompt_context import offer_text, profile_text, wishes_text

# Fixed on purpose: the candidate tunes their *wishes*, not the grid. If the model
# chose the weights, the total would drift between runs for invisible reasons.
AXIS_WEIGHTS: dict[str, float] = {
    "technical_match": 0.40,
    "seniority_scope": 0.20,
    "wishes": 0.25,
    "red_flags": 0.15,
}

AXIS_LABELS: dict[str, str] = {
    "technical_match": "Technical match",
    "seniority_scope": "Seniority and scope",
    "wishes": "Your wishes",
    "red_flags": "Red flags",
}

MAX_AXIS_SCORE = 5


def build_score(grid: ScoringGrid) -> Score:
    """Attach the weights and compute the total (0-100). In code, not by the model."""
    axes = [
        AxisScore(
            axis=axis,
            label=AXIS_LABELS[axis],
            weight=weight,
            score=getattr(grid, axis).score,
            justification=getattr(grid, axis).justification.strip(),
        )
        for axis, weight in AXIS_WEIGHTS.items()
    ]
    total_weight = sum(axis.weight for axis in axes) or 1.0
    weighted = sum(axis.score * axis.weight for axis in axes) / total_weight
    return Score(axes=axes, total=round(weighted / MAX_AXIS_SCORE * 100))


def content(*, offer: Offer, profile: Profile, wishes: list[Wish]) -> str:
    return (
        "## Candidate profile\n"
        f"{profile_text(profile)}\n\n"
        "## Candidate's wishes, with their weights\n"
        f"{wishes_text(wishes)}\n\n"
        "## Offer to evaluate\n"
        f"{offer_text(offer)}\n"
    )


async def score_offer(
    settings: Settings, *, offer: Offer, profile: Profile, wishes: list[Wish]
) -> Score:
    grid = await complete_structured(
        settings,
        schema=ScoringGrid,
        content=content(offer=offer, profile=profile, wishes=wishes),
        system=load_prompt("ranking/score.md"),
    )
    return build_score(grid)

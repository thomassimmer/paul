from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from app.config import Settings, Wish
from app.models import AxisVerdict, OfferDraft, Profile, ScoringGrid
from app.ranking import score


def _grid(**scores: int) -> ScoringGrid:
    values = {
        "technical_match": 0,
        "seniority_scope": 0,
        "wishes": 0,
        "red_flags": 0,
        **scores,
    }
    return ScoringGrid(**{axis: AxisVerdict(score=value) for axis, value in values.items()})


def test_weights_are_fixed_and_sum_to_one():
    assert sum(score.AXIS_WEIGHTS.values()) == pytest.approx(1.0)
    assert set(score.AXIS_WEIGHTS) == set(score.AXIS_LABELS)


def test_total_is_computed_in_code():
    result = score.build_score(_grid(technical_match=5, seniority_scope=4, wishes=3, red_flags=5))
    # 5*.40 + 4*.20 + 3*.25 + 5*.15 = 4.30 -> 86/100
    assert result.total == 86
    assert [axis.axis for axis in result.axes] == [
        "technical_match",
        "seniority_scope",
        "wishes",
        "red_flags",
    ]
    assert result.axes[0].label == "Technical match"
    assert result.axes[0].weight == 0.40


def test_bounds_of_the_total():
    assert score.build_score(_grid(technical_match=5, seniority_scope=5, wishes=5, red_flags=5)).total == 100
    assert score.build_score(_grid()).total == 0


def test_an_out_of_range_axis_is_rejected_by_validation():
    with pytest.raises(ValidationError):
        AxisVerdict(score=6)


def test_justifications_are_kept_per_axis():
    grid = ScoringGrid(technical_match=AxisVerdict(score=4, justification=" Rust everywhere "))
    result = score.build_score(grid)
    assert result.axes[0].justification == "Rust everywhere"


def test_score_offer_passes_the_wishes_and_keeps_the_code_weights(monkeypatch):
    seen: dict = {}

    async def fake(settings, *, schema, content, system=None, **kwargs):
        seen["content"] = content
        seen["system"] = system
        return _grid(technical_match=5, wishes=2)

    monkeypatch.setattr("app.ranking.score.complete_structured", fake)

    offer = OfferDraft(title="Senior Backend Engineer", company="Acme").to_offer([])
    profile = Profile()
    wishes = [Wish(label="startup", weight=3.0), Wish(label="remote")]

    result = asyncio.run(
        score.score_offer(Settings(model="openai/gpt-4o"), offer=offer, profile=profile, wishes=wishes)
    )

    assert result.total == 50  # (5*.40 + 0 + 2*.25 + 0) / 5 * 100
    assert "startup (weight 3)" in seen["content"]
    assert "remote (weight 1)" in seen["content"]
    assert "## Offer to evaluate" in seen["content"]
    assert seen["system"]


def test_offer_text_leaves_the_application_form_out():
    from app.models import FormQuestion

    offer = OfferDraft(title="Backend").to_offer([FormQuestion(label="Why us?", name="why")])
    from app.ranking.context import offer_text

    assert "Why us?" not in offer_text(offer)
    assert "Backend" in offer_text(offer)


def test_wishes_text_handles_none():
    from app.ranking.context import wishes_text

    assert wishes_text([]) == "(none given)"


def test_profile_text_omits_empty_fields():
    from app.ranking.context import profile_text

    text = profile_text(Profile(experiences=[]))
    assert "facts" not in text

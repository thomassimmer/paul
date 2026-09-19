from __future__ import annotations

import asyncio

from app.config import Settings
from app.models import Elimination, OfferDraft, Profile
from app.ranking import eliminate


def _offer():
    return OfferDraft(title="Senior Backend Engineer", company="Acme").to_offer([])


def test_an_elimination_needs_a_rule_and_an_excerpt():
    kept = eliminate.build_elimination(Elimination(eliminated=True, rule="No clearance", excerpt=""))
    assert kept.eliminated is False
    assert kept.rule == ""

    kept_bis = eliminate.build_elimination(Elimination(eliminated=True, rule="", excerpt="excerpt"))
    assert kept_bis.eliminated is False


def test_a_justified_elimination_is_kept():
    decision = Elimination(
        eliminated=True,
        rule="Eliminate offers requiring a security clearance.",
        excerpt="Candidates must hold an active SC clearance.",
    )
    assert eliminate.build_elimination(decision) == decision


def test_a_non_elimination_clears_the_fields():
    assert eliminate.build_elimination(Elimination(eliminated=False, rule="x", excerpt="y")) == Elimination()


def test_no_rules_means_no_elimination_and_no_model_call(monkeypatch):
    async def boom(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("the model must not be called without rules")

    monkeypatch.setattr("app.ranking.eliminate.complete_structured", boom)

    result = asyncio.run(
        eliminate.eliminate_offer(
            Settings(), offer=_offer(), profile=Profile(), rules="   "
        )
    )
    assert result == Elimination()


def test_rules_are_applied_through_the_model(monkeypatch):
    seen: dict = {}

    async def fake(settings, *, schema, content, system=None, **kwargs):
        seen["content"] = content
        return Elimination(
            eliminated=True,
            rule="Eliminate offers requiring a security clearance.",
            excerpt="active SC clearance",
        )

    monkeypatch.setattr("app.ranking.eliminate.complete_structured", fake)

    result = asyncio.run(
        eliminate.eliminate_offer(
            Settings(model="openai/gpt-4o"),
            offer=_offer(),
            profile=Profile(),
            rules="Eliminate offers requiring a security clearance.",
        )
    )

    assert result.eliminated is True
    assert "Eliminate offers requiring a security clearance." in seen["content"]
    assert "## Offer to evaluate" in seen["content"]

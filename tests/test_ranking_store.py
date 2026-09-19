from __future__ import annotations

import pytest

from app.models import Elimination, OfferDraft, Score, ScoringGrid
from app.offers import store as offers_store
from app.ranking import score, store


def _offer() -> int:
    return offers_store.save_offer(
        OfferDraft(title="Senior Backend Engineer").to_offer([]), raw="", cleaned="", source="text"
    ).id


def _score(total: int = 80) -> Score:
    grid = ScoringGrid()
    result = score.build_score(grid)
    result.total = total
    return result


def test_save_and_load_round_trip():
    offer_id = _offer()
    record = store.save_ranking(
        offer_id,
        elimination=Elimination(eliminated=True, rule="A rule", excerpt="An excerpt"),
        override="",
        score=None,
    )
    assert record.offer_id == offer_id
    assert record.eliminated is True
    assert record.elimination.rule == "A rule"
    assert record.score is None

    loaded = store.load_ranking(offer_id)
    assert loaded is not None
    assert loaded.elimination.excerpt == "An excerpt"


def test_saving_a_score_replaces_the_previous_one():
    offer_id = _offer()
    store.save_ranking(offer_id, elimination=Elimination(), override="", score=_score(70))
    store.save_ranking(offer_id, elimination=Elimination(), override="", score=_score(91))

    loaded = store.load_ranking(offer_id)
    assert loaded is not None
    assert loaded.score is not None
    assert loaded.score.total == 91
    assert len(loaded.score.axes) == 4


def test_list_rankings_is_keyed_by_offer():
    first, second = _offer(), _offer()
    store.save_ranking(first, elimination=Elimination(), override="", score=_score())
    rankings = store.list_rankings()
    assert set(rankings) == {first}
    assert second not in rankings


def test_keeping_an_offer_overrides_the_rules_and_keeps_the_score():
    offer_id = _offer()
    store.save_ranking(
        offer_id,
        elimination=Elimination(eliminated=True, rule="r", excerpt="e"),
        override="",
        score=None,
    )
    record = store.set_override(offer_id, "kept")
    assert record.eliminated is False
    assert record.override == "kept"

    store.save_ranking(offer_id, elimination=Elimination(), override="kept", score=_score(88))
    assert store.set_override(offer_id, "kept").score is not None


def test_eliminating_by_hand_drops_the_score():
    offer_id = _offer()
    store.save_ranking(offer_id, elimination=Elimination(), override="", score=_score(88))
    record = store.set_override(offer_id, "eliminated")
    assert record.eliminated is True
    assert record.score is None


def test_resetting_returns_to_the_rules_verdict():
    offer_id = _offer()
    store.save_ranking(
        offer_id,
        elimination=Elimination(eliminated=True, rule="r", excerpt="e"),
        override="kept",
        score=_score(88),
    )
    record = store.set_override(offer_id, "")
    assert record.override == ""
    assert record.eliminated is True  # the rules say so again
    assert record.score is not None  # the score itself is not touched


def test_an_unknown_override_is_refused():
    offer_id = _offer()
    with pytest.raises(ValueError):
        store.set_override(offer_id, "maybe")


def test_deleting_an_offer_deletes_its_ranking():
    offer_id = _offer()
    store.save_ranking(offer_id, elimination=Elimination(), override="", score=_score())
    offers_store.delete_offer(offer_id)
    assert store.load_ranking(offer_id) is None

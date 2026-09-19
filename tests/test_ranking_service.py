from __future__ import annotations

import asyncio

from app.config import Settings, Wish
from app.llm import LLMError
from app.models import Elimination, Identity, Offer, OfferDraft, Profile, RankingRecord, Score, ScoringGrid
from app.offers import store as offers_store
from app.ranking import score, service, store

SETTINGS = Settings(
    model="openai/gpt-4o",
    filter_rules="Eliminate offers requiring a security clearance.",
    wishes=[Wish(label="remote")],
)
PROFILE = Profile()


def _offer_model(title: str = "An offer", company: str = "Acme") -> Offer:
    return OfferDraft(title=title, company=company).to_offer([])


def _saved(title: str) -> int:
    return offers_store.save_offer(
        _offer_model(title), raw="", cleaned="cleaned", source="text"
    ).id


def _stored(offer_id: int) -> RankingRecord:
    record = store.load_ranking(offer_id)
    assert record is not None
    return record


def _score_with(total: int) -> Score:
    result = score.build_score(ScoringGrid())
    result.total = total
    return result


def _patch(monkeypatch, *, eliminated: bool = False, total: int = 80, score_error=None):
    async def fake_eliminate(settings, *, offer, profile, rules):
        if eliminated:
            return Elimination(eliminated=True, rule="A rule", excerpt="An excerpt")
        return Elimination()

    async def fake_score(settings, *, offer, profile, wishes):
        if score_error is not None:
            raise score_error
        return _score_with(total)

    monkeypatch.setattr("app.ranking.eliminate.eliminate_offer", fake_eliminate)
    monkeypatch.setattr("app.ranking.score.score_offer", fake_score)


# --- fingerprint and staleness ------------------------------------------------


def test_fingerprint_is_stable_and_reacts_to_every_input():
    offer = _offer_model()
    base = service.fingerprint(SETTINGS, PROFILE, offer)

    assert service.fingerprint(SETTINGS, PROFILE, offer) == base
    assert service.fingerprint(SETTINGS.model_copy(update={"filter_rules": "x"}), PROFILE, offer) != base
    assert service.fingerprint(
        SETTINGS.model_copy(update={"wishes": [Wish(label="startup", weight=2)]}), PROFILE, offer
    ) != base
    assert service.fingerprint(SETTINGS.model_copy(update={"model": "openai/gpt-4o-mini"}), PROFILE, offer) != base
    assert service.fingerprint(SETTINGS, Profile(identity=Identity(name="Camille")), offer) != base
    assert service.fingerprint(SETTINGS, PROFILE, _offer_model(title="Another")) != base


def test_is_stale_only_for_an_existing_outdated_ranking():
    offer = _offer_model()
    assert service.is_stale(SETTINGS, PROFILE, offer, None) is False

    fresh = RankingRecord(offer_id=1, fingerprint=service.fingerprint(SETTINGS, PROFILE, offer))
    assert service.is_stale(SETTINGS, PROFILE, offer, fresh) is False

    outdated = RankingRecord(offer_id=1, fingerprint="0000000000000000")
    assert service.is_stale(SETTINGS, PROFILE, offer, outdated) is True


# --- scope selection ----------------------------------------------------------


def test_pending_means_never_ranked_or_out_of_date():
    first, second = _saved("First"), _saved("Second")
    offers = offers_store.list_offers()

    assert {offer.id for offer in service.select_offers("pending", offers, {}, SETTINGS, PROFILE)} == {
        first,
        second,
    }

    for record in offers:
        store.save_ranking(
            record.id,
            elimination=Elimination(),
            override="",
            score=_score_with(70),
            fingerprint=service.fingerprint(SETTINGS, PROFILE, record.offer),
        )
    rankings = store.list_rankings()

    assert service.select_offers("pending", offers, rankings, SETTINGS, PROFILE) == []
    assert len(service.select_offers("all", offers, rankings, SETTINGS, PROFILE)) == 2
    assert [
        offer.id
        for offer in service.select_offers("selected", offers, rankings, SETTINGS, PROFILE, [second])
    ] == [second]


def test_changing_a_wish_puts_the_offers_back_in_scope():
    offer_id = _saved("First")
    offers = offers_store.list_offers()
    record = offers[0]
    store.save_ranking(
        offer_id,
        elimination=Elimination(),
        override="",
        score=_score_with(70),
        fingerprint=service.fingerprint(SETTINGS, PROFILE, record.offer),
    )
    rankings = store.list_rankings()

    assert service.select_offers("pending", offers, rankings, SETTINGS, PROFILE) == []

    changed = SETTINGS.model_copy(update={"wishes": [Wish(label="startup", weight=5)]})
    assert [offer.id for offer in service.select_offers("pending", offers, rankings, changed, PROFILE)] == [
        offer_id
    ]


# --- one offer ----------------------------------------------------------------


def test_rank_one_stores_the_score_and_its_fingerprint(monkeypatch):
    _patch(monkeypatch, total=73)
    offer_id = _saved("A")
    record = offers_store.load_offer(offer_id)
    assert record is not None

    outcome = asyncio.run(service.rank_one(SETTINGS, PROFILE, record))

    assert outcome.status == "scored"
    assert outcome.total == 73
    stored = _stored(offer_id)
    assert stored.score is not None and stored.score.total == 73
    assert stored.fingerprint == service.fingerprint(SETTINGS, PROFILE, record.offer)


def test_rank_one_stores_an_elimination_without_a_score(monkeypatch):
    _patch(monkeypatch, eliminated=True)
    offer_id = _saved("A")
    record = offers_store.load_offer(offer_id)
    assert record is not None

    outcome = asyncio.run(service.rank_one(SETTINGS, PROFILE, record))

    assert outcome.status == "eliminated"
    stored = _stored(offer_id)
    assert stored.eliminated is True
    assert stored.score is None


def test_a_model_failure_leaves_the_offer_to_do(monkeypatch):
    _patch(monkeypatch, score_error=LLMError("provider is down"))
    offer_id = _saved("A")
    record = offers_store.load_offer(offer_id)
    assert record is not None

    outcome = asyncio.run(service.rank_one(SETTINGS, PROFILE, record))

    assert outcome.status == "error"
    assert "provider is down" in outcome.error
    # Nothing written: the offer stays in the pending scope for the next run.
    assert store.load_ranking(offer_id) is None


def test_a_manual_override_reuses_the_evidence_and_skips_the_rules(monkeypatch):
    offer_id = _saved("A")
    record = offers_store.load_offer(offer_id)
    assert record is not None
    store.save_ranking(
        offer_id,
        elimination=Elimination(eliminated=True, rule="A rule", excerpt="An excerpt"),
        override="kept",
        score=None,
    )

    async def forbidden_eliminate(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("the rules must not be applied again for a manual override")

    async def fake_score(settings, *, offer, profile, wishes):
        return _score_with(91)

    monkeypatch.setattr("app.ranking.eliminate.eliminate_offer", forbidden_eliminate)
    monkeypatch.setattr("app.ranking.score.score_offer", fake_score)

    outcome = asyncio.run(service.rank_one(SETTINGS, PROFILE, record, previous=_stored(offer_id)))

    assert outcome.status == "scored"
    stored = _stored(offer_id)
    assert stored.elimination.rule == "A rule"  # the evidence is kept for display
    assert stored.override == "kept"


# --- the table ----------------------------------------------------------------


def test_rows_order_the_shortlist():
    weak, strong, eliminated = _saved("Weak"), _saved("Strong"), _saved("Eliminated")
    _saved("Unranked")

    store.save_ranking(weak, elimination=Elimination(), override="", score=_score_with(40))
    store.save_ranking(strong, elimination=Elimination(), override="", score=_score_with(90))
    store.save_ranking(
        eliminated,
        elimination=Elimination(eliminated=True, rule="A rule", excerpt="An excerpt"),
        override="",
        score=None,
    )

    rows = service.ranking_rows(
        offers_store.list_offers(), store.list_rankings(), SETTINGS, PROFILE
    )

    assert [row["offer"].offer.title for row in rows] == [
        "Strong",
        "Weak",
        "Unranked",
        "Eliminated",
    ]
    assert [row["total"] for row in rows[:3]] == [90, 40, None]
    assert rows[2]["needs_score"] is False  # never ranked is not "needs a score"


def test_rows_mark_outdated_scores():
    offer_id = _saved("A")
    record = offers_store.load_offer(offer_id)
    assert record is not None
    store.save_ranking(
        offer_id,
        elimination=Elimination(),
        override="",
        score=_score_with(80),
        fingerprint=service.fingerprint(SETTINGS, PROFILE, record.offer),
    )

    rows = service.ranking_rows(offers_store.list_offers(), store.list_rankings(), SETTINGS, PROFILE)
    assert rows[0]["stale"] is False

    changed = SETTINGS.model_copy(update={"filter_rules": "Something else entirely."})
    rows = service.ranking_rows(offers_store.list_offers(), store.list_rankings(), changed, PROFILE)
    assert rows[0]["stale"] is True

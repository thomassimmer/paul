from __future__ import annotations

from app.models import FormQuestion, Offer, OfferDraft
from app.offers import store


def _offer(title: str = "Senior Backend Engineer") -> Offer:
    return OfferDraft(title=title, company="Acme").to_offer(
        [FormQuestion(label="Why us?", name="why")]
    )


def test_save_and_load_round_trip():
    record = store.save_offer(_offer(), raw="<div>x</div>", cleaned="x", source="html")
    assert record.id > 0
    assert record.source == "html"
    assert record.analyzed_at
    assert record.offer.title == "Senior Backend Engineer"

    loaded = store.load_offer(record.id)
    assert loaded is not None
    assert loaded.offer.form[0].label == "Why us?"


def test_source_is_kept_for_a_later_re_run():
    record = store.save_offer(_offer(), raw="RAW FRAGMENT", cleaned="CLEANED TEXT", source="html")
    assert store.load_source(record.id) == ("RAW FRAGMENT", "CLEANED TEXT")


def test_list_is_newest_first_and_counted():
    store.save_offer(_offer("First"), raw="", cleaned="", source="text")
    second = store.save_offer(_offer("Second"), raw="", cleaned="", source="text")

    assert [record.offer.title for record in store.list_offers()] == ["Second", "First"]
    assert store.list_offers()[0].id == second.id
    assert store.count_offers() == 2


def test_update_replaces_the_extracted_offer():
    record = store.save_offer(_offer("Old"), raw="", cleaned="", source="text")
    store.update_offer(record.id, _offer("New"))
    updated = store.load_offer(record.id)
    assert updated is not None
    assert updated.offer.title == "New"


def test_the_posting_url_is_saved_and_survives_a_re_analysis():
    record = store.save_offer(_offer("Old"), raw="", cleaned="", source="text")
    assert record.url == ""  # the analyzer never sees the address bar

    store.update_offer(record.id, _offer("New"), url="https://example.com/jobs/42")
    saved = store.load_offer(record.id)
    assert saved is not None
    assert saved.url == "https://example.com/jobs/42"

    # A re-analysis replaces the offer but knows nothing about the link, so it is kept.
    store.update_offer(record.id, _offer("Newest"))
    kept = store.load_offer(record.id)
    assert kept is not None
    assert kept.url == "https://example.com/jobs/42"


def test_delete():
    record = store.save_offer(_offer(), raw="", cleaned="", source="text")
    store.delete_offer(record.id)
    assert store.load_offer(record.id) is None
    assert store.count_offers() == 0


def test_loading_a_missing_offer_returns_none():
    assert store.load_offer(1234) is None
    assert store.load_source(1234) is None

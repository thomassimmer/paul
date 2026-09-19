from __future__ import annotations

from datetime import date

from app.models import Application, OfferDraft
from app.offers import store as offers_store
from app.tracker import store

TODAY = date(2026, 9, 19)


def _offer(title: str = "An offer") -> int:
    return offers_store.save_offer(
        OfferDraft(title=title, company="Acme").to_offer([]), raw="", cleaned="", source="text"
    ).id


def test_no_row_means_no_application():
    offer_id = _offer()
    assert store.load_application(offer_id) is None
    assert store.list_applications() == {}


def test_save_and_load_round_trip():
    offer_id = _offer()
    saved = store.save_application(
        offer_id,
        status="interview",
        applied_on="2026-09-01",
        last_contact="2026-09-10",
        notes="Spoke to the CTO",
    )
    assert saved.status == "interview"

    loaded = store.load_application(offer_id)
    assert loaded is not None
    assert loaded.applied_on == "2026-09-01"
    assert loaded.last_contact == "2026-09-10"
    assert loaded.notes == "Spoke to the CTO"
    assert loaded.updated_at


def test_saving_again_replaces_the_record():
    offer_id = _offer()
    store.save_application(offer_id, status="ready", applied_on="", last_contact="", notes="a")
    store.save_application(offer_id, status="offer", applied_on="", last_contact="", notes="b")

    loaded = store.load_application(offer_id)
    assert loaded is not None
    assert (loaded.status, loaded.notes) == ("offer", "b")
    assert len(store.list_applications()) == 1


def test_set_status_keeps_the_dates_and_notes():
    offer_id = _offer()
    store.save_application(
        offer_id, status="ready", applied_on="", last_contact="2026-09-10", notes="keep me"
    )

    store.set_status(offer_id, "interview", today=TODAY)

    loaded = store.load_application(offer_id)
    assert loaded is not None
    assert loaded.status == "interview"
    assert loaded.last_contact == "2026-09-10"
    assert loaded.notes == "keep me"


def test_set_status_to_applied_records_today():
    offer_id = _offer()
    store.set_status(offer_id, "applied", today=TODAY)

    loaded = store.load_application(offer_id)
    assert loaded is not None
    assert loaded.applied_on == "2026-09-19"


def test_set_status_normalizes_an_unknown_value():
    offer_id = _offer()
    store.set_status(offer_id, "who knows", today=TODAY)
    loaded = store.load_application(offer_id)
    assert loaded is not None
    assert loaded.status == "analyzed"


def test_applied_offer_ids_feeds_the_ranker():
    applied = _offer("Applied")
    rejected = _offer("Rejected")
    pending = _offer("Not applied")

    store.set_status(applied, "applied", today=TODAY)
    store.set_status(rejected, "rejected", today=TODAY)

    assert store.applied_offer_ids() == {applied, rejected}
    assert pending not in store.applied_offer_ids()


def test_clearing_goes_back_to_the_default():
    offer_id = _offer()
    store.set_status(offer_id, "applied", today=TODAY)
    store.delete_application(offer_id)
    assert store.load_application(offer_id) is None
    assert store.applied_offer_ids() == set()


def test_deleting_an_offer_deletes_its_application():
    offer_id = _offer()
    store.set_status(offer_id, "applied", today=TODAY)
    offers_store.delete_offer(offer_id)
    assert store.load_application(offer_id) is None


def test_an_application_defaults_to_analyzed():
    assert Application(offer_id=1).status == "analyzed"

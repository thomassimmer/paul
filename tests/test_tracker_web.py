"""Tracking: the status of one offer, and its dates and notes.

The status select is in a board row, the rest is a section of the offer page; the
two routes below are what changes them.
"""

from __future__ import annotations

from app.config import Settings, save_settings
from app.models import Application, Offer, OfferDraft
from app.offers import store as offers_store
from app.tracker import store


def _seed_offer(title: str = "Senior Backend Engineer") -> int:
    offer: Offer = OfferDraft(title=title, company="Acme", location="Lyon").to_offer([])
    return offers_store.save_offer(offer, raw="", cleaned="cleaned", source="text").id


def _seed_application(offer_id: int, **fields) -> Application:
    base = {"status": "analyzed", "applied_on": "", "last_contact": "", "notes": ""}
    base.update(fields)
    return store.save_application(offer_id, **base)


# --- the tracking form on the offer page --------------------------------------


def test_the_offer_page_renders_the_tracking_form(client):
    offer_id = _seed_offer()
    response = client.get(f"/offers/{offer_id}")

    assert response.status_code == 200
    assert 'id="tracking"' in response.text
    assert 'name="applied_on"' in response.text
    assert 'name="last_contact"' in response.text
    assert 'name="notes"' in response.text
    assert "has not been ranked" in response.text


def test_the_offer_page_saves_dates_and_notes(client):
    offer_id = _seed_offer()

    response = client.post(
        f"/tracker/{offer_id}",
        data={
            "status": "interview",
            "applied_on": "2026-09-01",
            "last_contact": "2026-09-10",
            "notes": "Spoke to the CTO.",
        },
        follow_redirects=True,
    )

    assert "Application saved" in response.text
    application = store.load_application(offer_id)
    assert application is not None
    assert application.status == "interview"
    assert application.applied_on == "2026-09-01"
    assert application.notes == "Spoke to the CTO."


def test_a_bad_date_is_refused(client):
    offer_id = _seed_offer()

    response = client.post(
        f"/tracker/{offer_id}",
        data={"status": "applied", "applied_on": "01/09/2026", "last_contact": "", "notes": ""},
        follow_redirects=True,
    )

    assert "must look like" in response.text
    assert store.load_application(offer_id) is None


def test_an_application_can_be_reset(client):
    offer_id = _seed_offer()
    _seed_application(offer_id, status="applied", applied_on="2020-01-01")

    response = client.post(f"/tracker/{offer_id}/clear", follow_redirects=True)

    assert "reset to Analyzed" in response.text
    assert store.load_application(offer_id) is None


# --- the sections of the offer page -------------------------------------------


def test_the_offer_page_offers_to_prepare_the_documents(client):
    offer_id = _seed_offer()
    page = client.get(f"/offers/{offer_id}").text
    # The prompt to prepare, and the button that actually prepares.
    assert "Tailored documents" in page
    assert f'action="/applications/{offer_id}/prepare"' in page


def test_the_offer_page_navigates_to_the_tracking(client):
    offer_id = _seed_offer()
    page = client.get(f"/offers/{offer_id}").text
    assert 'href="#tracking"' in page
    assert 'href="#offer"' in page
    assert 'href="#ranking"' in page


def test_followup_delay_is_repeated_on_the_tracking_form(client):
    save_settings(Settings(followup_days=12))
    offer_id = _seed_offer()
    assert "after 12 days" in client.get(f"/offers/{offer_id}").text


def test_a_missing_offer_is_reported(client):
    # The old detail URL lands on the offer page, which reports the missing offer.
    assert "no longer exists" in client.get("/tracker/999", follow_redirects=True).text

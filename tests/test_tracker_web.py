from __future__ import annotations

from app.config import Settings, save_settings
from app.models import Application, Offer, OfferDraft
from app.offers import store as offers_store
from app.tracker import service, store


def _seed_offer(title: str = "Senior Backend Engineer") -> int:
    offer: Offer = OfferDraft(title=title, company="Acme", location="Lyon").to_offer([])
    return offers_store.save_offer(offer, raw="", cleaned="cleaned", source="text").id


def _seed_application(offer_id: int, **fields) -> Application:
    base = {
        "status": "analyzed",
        "applied_on": "",
        "last_contact": "",
        "notes": "",
    }
    base.update(fields)
    return store.save_application(offer_id, **base)


# --- the board ----------------------------------------------------------------


def test_the_tracker_starts_empty(client):
    response = client.get("/tracker")
    assert response.status_code == 200
    assert "Nothing here yet" in response.text


def test_analyzed_offers_show_up_with_the_default_status(client):
    _seed_offer()
    response = client.get("/tracker")

    assert "Senior Backend Engineer" in response.text
    assert "Acme" in response.text
    assert '<option value="analyzed" selected>' in response.text


def test_a_status_can_be_changed_from_the_board(client):
    offer_id = _seed_offer()

    response = client.post(
        f"/tracker/{offer_id}/status", data={"status": "applied"}, follow_redirects=True
    )

    assert "Status set to Applied" in response.text
    assert "follow-up clock" in response.text
    application = store.load_application(offer_id)
    assert application is not None
    assert application.status == "applied"
    assert application.applied_on  # filled in for you


def test_changing_the_status_keeps_the_current_view(client):
    offer_id = _seed_offer()
    response = client.post(
        f"/tracker/{offer_id}/status",
        data={"status": "shortlisted", "sort": "company", "dir": "asc", "filter": "not_applied"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "sort=company" in response.headers["location"]
    assert "dir=asc" in response.headers["location"]
    assert "status=not_applied" in response.headers["location"]


def test_the_row_form_has_only_one_field_named_status(client):
    """A second ``status`` field would shadow the select and break every change."""
    offer_id = _seed_offer()
    page = client.get("/tracker").text
    start = page.index(f'action="/tracker/{offer_id}/status"')
    form = page[start : page.index("</form>", start)]
    assert form.count('name="status"') == 1


def test_the_board_status_form_sends_what_the_server_reads(client):
    """Replay the exact fields the template renders."""
    offer_id = _seed_offer()
    response = client.post(
        f"/tracker/{offer_id}/status",
        data={"sort": "score", "dir": "desc", "filter": "all", "status": "applied"},
        follow_redirects=True,
    )

    assert "Status set to Applied" in response.text
    application = store.load_application(offer_id)
    assert application is not None
    assert application.status == "applied"
    assert application.applied_on


def test_a_followup_is_highlighted_after_the_delay(client):
    offer_id = _seed_offer()
    _seed_application(offer_id, status="applied", applied_on="2020-01-01")

    response = client.get("/tracker")

    assert "Follow up" in response.text
    assert "days without news" in response.text
    assert 'class="due"' in response.text


def test_the_delay_comes_from_the_settings(client):
    offer_id = _seed_offer()
    _seed_application(offer_id, status="applied", applied_on=service.today_utc().isoformat())

    assert "Follow up" not in client.get("/tracker").text

    save_settings(Settings(followup_days=0))
    assert "Follow up" in client.get("/tracker").text


def test_the_board_can_be_filtered(client):
    applied = _seed_offer("Applied role")
    _seed_offer("Fresh role")
    _seed_application(applied, status="applied", applied_on="2020-01-01")

    response = client.get("/tracker?status=not_applied")
    assert "Fresh role" in response.text
    assert "Applied role" not in response.text

    response = client.get("/tracker?status=applied")
    assert "Applied role" in response.text
    assert "Fresh role" not in response.text

    response = client.get("/tracker?status=followup")
    assert "Applied role" in response.text
    assert "Fresh role" not in response.text


def test_the_board_can_be_sorted(client):
    _seed_offer("Alpha role")
    _seed_offer("Beta role")

    asc = client.get("/tracker?sort=role&dir=asc")
    assert asc.text.index("Alpha role") < asc.text.index("Beta role")

    desc = client.get("/tracker?sort=role&dir=desc")
    assert desc.text.index("Beta role") < desc.text.index("Alpha role")


# --- one application ----------------------------------------------------------


def test_the_detail_page_renders_the_form(client):
    offer_id = _seed_offer()
    response = client.get(f"/tracker/{offer_id}")

    assert response.status_code == 200
    assert 'name="applied_on"' in response.text
    assert 'name="last_contact"' in response.text
    assert 'name="notes"' in response.text
    assert "has not been ranked" in response.text


def test_the_detail_page_saves_dates_and_notes(client):
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


def test_a_missing_offer_is_reported(client):
    assert "no longer exists" in client.get("/tracker/999").text
    assert "no longer exists" in client.post("/tracker/999/status", data={"status": "ready"}).text


def test_the_nav_links_to_the_tracker(client):
    assert 'href="/tracker"' in client.get("/").text

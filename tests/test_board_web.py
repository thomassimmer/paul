"""The board: the one page that lists offers.

Filtering, sorting, the status select of a row, the selection the ranking uses,
the ranking modal and the onboarding checklist all live on this page, so they are
tested together here.
"""

from __future__ import annotations

from app.config import Settings, Wish, load_settings, save_settings
from app.models import (
    Application,
    Elimination,
    Identity,
    Offer,
    OfferDraft,
    Profile,
    ScoringGrid,
)
from app.offers import store as offers_store
from app.profiler import store as profile_store
from app.ranking import jobs as ranking_jobs
from app.ranking import score
from app.ranking import store as ranking_store
from app.tracker import service as tracker_service
from app.tracker import store as tracker_store
from app.writer import jobs as writer_jobs
from app.writer import store as writer_store

MODEL = "openai/gpt-4o"


def _seed_offer(title: str = "Senior Backend Engineer") -> int:
    offer: Offer = OfferDraft(title=title, company="Acme", location="Lyon").to_offer([])
    return offers_store.save_offer(offer, raw="", cleaned="cleaned", source="text").id


def _seed_profile() -> None:
    profile_store.save_profile(Profile(identity=Identity(name="Camille Moreau")))


def _configure(**overrides) -> None:
    base = {
        "model": MODEL,
        "filter_rules": "Eliminate offers requiring a security clearance.",
        "wishes": [Wish(label="remote", weight=2.0)],
    }
    base.update(overrides)
    save_settings(Settings(**base))


def _seed_application(
    offer_id: int,
    *,
    status: str = "analyzed",
    applied_on: str = "",
    last_contact: str = "",
    notes: str = "",
) -> Application:
    return tracker_store.save_application(
        offer_id,
        status=status,
        applied_on=applied_on,
        last_contact=last_contact,
        notes=notes,
    )


def _seed_ranking(offer_id: int, *, total: int = 74, eliminated: bool = False) -> None:
    result = score.build_score(ScoringGrid())
    result.total = total
    ranking_store.save_ranking(
        offer_id,
        elimination=Elimination(eliminated=eliminated, rule="A rule", excerpt="An excerpt"),
        override="",
        score=None if eliminated else result,
        fingerprint="whatever",
    )


# --- the page -----------------------------------------------------------------


def test_the_board_renders_when_empty(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "No offer analyzed yet" in response.text
    assert "Analyze an offer" in response.text


def test_the_board_lists_each_offer_with_its_score_and_verdict(client):
    ranked = _seed_offer("Ranked role")
    gone = _seed_offer("Eliminated role")
    _seed_offer("Fresh role")
    _seed_ranking(ranked, total=74)
    _seed_ranking(gone, eliminated=True)

    page = client.get("/").text

    assert "Ranked role" in page
    assert "74" in page
    assert "Kept" in page
    assert "Eliminated" in page
    assert "not ranked" in page  # the third offer


def test_a_row_shows_the_default_status(client):
    _seed_offer()
    assert '<option value="analyzed" selected>' in client.get("/").text


def test_the_board_row_has_no_prepare_button_or_documents_column(client):
    """Preparing is decided on the offer page, where the dialog lives."""
    _seed_offer()
    page = client.get("/").text

    assert "<th>Documents</th>" not in page
    assert ">Prepare<" not in page
    assert "/applications/" not in page


def test_the_board_can_be_filtered(client):
    applied = _seed_offer("Applied role")
    _seed_offer("Fresh role")
    _seed_application(applied, status="applied", applied_on="2020-01-01")

    response = client.get("/?status=not_applied")
    assert "Fresh role" in response.text
    assert "Applied role" not in response.text

    response = client.get("/?status=applied")
    assert "Applied role" in response.text
    assert "Fresh role" not in response.text

    response = client.get("/?status=followup")
    assert "Applied role" in response.text
    assert "Fresh role" not in response.text


def test_the_board_can_be_sorted(client):
    _seed_offer("Alpha role")
    _seed_offer("Beta role")

    asc = client.get("/?sort=role&dir=asc")
    assert asc.text.index("Alpha role") < asc.text.index("Beta role")

    desc = client.get("/?sort=role&dir=desc")
    assert desc.text.index("Beta role") < desc.text.index("Alpha role")


def test_a_followup_is_highlighted_after_the_delay(client):
    offer_id = _seed_offer()
    _seed_application(offer_id, status="applied", applied_on="2020-01-01")

    response = client.get("/")

    assert "Follow up" in response.text
    assert "days without news" in response.text
    assert 'class="due"' in response.text


def test_the_delay_comes_from_the_settings(client):
    offer_id = _seed_offer()
    _seed_application(offer_id, status="applied", applied_on=tracker_service.today_utc().isoformat())

    assert "Follow up" not in client.get("/").text

    save_settings(Settings(followup_days=0))
    assert "Follow up" in client.get("/").text


# --- the status, changed from a row -------------------------------------------


def test_a_status_can_be_changed_from_the_board(client):
    offer_id = _seed_offer()

    response = client.post(
        f"/tracker/{offer_id}/status", data={"status": "applied"}, follow_redirects=True
    )

    assert "Status set to Applied" in response.text
    assert "follow-up clock" in response.text
    application = tracker_store.load_application(offer_id)
    assert application is not None
    assert application.status == "applied"
    assert application.applied_on  # filled in for you


def test_changing_the_status_keeps_the_current_view(client):
    offer_id = _seed_offer()
    response = client.post(
        f"/tracker/{offer_id}/status",
        data={"status": "shortlisted", "sort": "role", "dir": "asc", "filter": "not_applied"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert "sort=role" in location
    assert "dir=asc" in location
    assert "status=not_applied" in location


def test_the_row_form_has_only_one_field_named_status(client):
    """A second ``status`` field would shadow the select and break every change."""
    offer_id = _seed_offer()
    page = client.get("/").text
    start = page.index(f'action="/tracker/{offer_id}/status"')
    form = page[start : page.index("</form>", start)]
    assert form.count('name="status"') == 1


# --- the selection the ranking run uses ---------------------------------------


def test_each_row_carries_a_tick_bound_to_the_run_form(client):
    offer_id = _seed_offer()
    page = client.get("/").text

    assert f'name="offer_ids" value="{offer_id}" form="run-form"' in page
    assert 'id="run-form"' in page


def test_a_missing_offer_is_reported(client):
    response = client.post("/tracker/999/status", data={"status": "ready"}, follow_redirects=True)
    assert "no longer exists" in response.text


# --- the ranking modal --------------------------------------------------------


def test_the_modal_holds_the_criteria_and_the_run(client):
    _seed_offer()
    page = client.get("/").text

    assert 'id="ranking-modal"' in page
    assert 'name="filter_rules"' in page
    assert 'name="wishes"' in page
    assert 'name="concurrency"' in page
    assert 'name="scope"' in page


def test_the_modal_warns_without_a_profile(client):
    _seed_offer()
    assert "Import your CV" in client.get("/").text


def test_the_offers_are_ranked_from_the_modal(client, monkeypatch):
    """The whole run lives in the dialog, but it posts to the same route as before."""
    _seed_profile()
    _seed_offer()
    _configure()

    async def fake_eliminate(settings, *, offer, profile, rules):
        return Elimination()

    async def fake_score(settings, *, offer, profile, wishes):
        result = score.build_score(ScoringGrid())
        result.total = 61
        return result

    async def inline(settings, profile, records, rankings, *, scope, concurrency):
        job = ranking_jobs.remember(
            ranking_jobs.build_job(records, scope=scope, concurrency=concurrency)
        )
        assert job is not None
        await ranking_jobs.run(job, settings, profile, records, rankings)
        return job

    monkeypatch.setattr("app.ranking.eliminate.eliminate_offer", fake_eliminate)
    monkeypatch.setattr("app.ranking.score.score_offer", fake_score)
    monkeypatch.setattr("app.ranking.jobs.start_job", inline)

    response = client.post("/ranking/run", follow_redirects=True)

    assert "61" in response.text
    assert "Last run" in response.text


# --- the two job panels -------------------------------------------------------


def test_the_ranking_panel_polls_and_refreshes_the_table(client, monkeypatch):
    _seed_profile()
    _seed_offer()
    _configure()
    _seed_ranking(_seed_offer("Another role"))
    job = ranking_jobs.Job(
        scope="all", concurrency=2, offers=[ranking_jobs.JobOffer(offer_id=1, title="A", company="B")]
    )
    ranking_jobs.remember(job)

    page = client.get("/").text
    assert 'hx-get="/progress/ranking?sort=' in page
    assert 'hx-trigger="every 1s"' in page

    response = client.get("/progress/ranking")
    assert response.status_code == 200
    assert 'id="job-ranking"' in response.text
    assert 'id="board-table" hx-swap-oob="outerHTML"' in response.text
    assert "Senior Backend Engineer" in response.text


def test_the_preparation_panel_polls_and_refreshes_the_table(client, monkeypatch):
    _seed_profile()
    offer_id = _seed_offer()
    _configure()
    record = offers_store.load_offer(offer_id)
    assert record is not None
    writer_jobs.remember(writer_jobs.build_job(record, kind="prepare"))

    page = client.get("/").text
    assert 'hx-get="/progress/writing?sort=' in page
    assert "Preparation in progress" in page
    # The board cannot start a preparation any more: the button lives on the offer
    # page, behind its dialog.
    assert "/prepare" not in page
    # Stop refreshes the board's own panel, whose poll URL carries the table state.
    assert 'hx-target="#job-writing"' in page
    assert 'name="poll_url" value="/progress/writing?sort=' in page
    # The board keeps its panel inline; only the offer page floats it.
    assert 'class="job-toast"' not in page

    response = client.get("/progress/writing")
    assert response.status_code == 200
    assert 'id="job-writing"' in response.text
    assert 'id="board-table" hx-swap-oob="outerHTML"' in response.text


def test_a_finished_job_stops_polling(client, monkeypatch):
    _seed_profile()
    offer_id = _seed_offer()
    _configure()
    record = offers_store.load_offer(offer_id)
    assert record is not None
    job = writer_jobs.build_job(record, kind="prepare")
    job.status = "done"
    writer_jobs.remember(job)

    page = client.get("/").text
    assert "Preparation in progress" not in page
    assert "Last preparation" in page
    assert 'hx-get="/progress/writing' not in page
    # From the board the finished panel points at the offer, so its documents can be
    # reviewed.
    assert "Open the offer" in page
    # And it stays put: only the offer page's toast closes itself once done.
    assert 'hx-trigger="load delay:1s"' not in page


# --- the onboarding checklist -------------------------------------------------


def test_the_checklist_is_open_on_a_fresh_install(client):
    assert 'id="get-started" open' in client.get("/").text


def test_the_checklist_disappears_once_everything_is_done(client):
    _configure()
    _seed_profile()
    offer_id = _seed_offer()
    _seed_ranking(offer_id)
    folder = writer_store.create("2026-09-acme-senior-backend-engineer")
    tracker_store.set_folder(offer_id, folder.name)
    tracker_store.set_status(offer_id, "applied", today=tracker_service.today_utc())

    page = client.get("/").text
    # A finished checklist leaves nothing behind.
    assert 'id="get-started"' not in page


def test_the_checklist_can_be_hidden_before_it_is_finished(client):
    response = client.post("/get-started/dismiss", follow_redirects=False)
    assert response.status_code == 303
    assert load_settings().show_get_started is False

    page = client.get("/").text
    assert 'id="get-started"' not in page


def test_settings_brings_the_checklist_back(client):
    client.post("/get-started/dismiss")
    assert 'id="get-started"' not in client.get("/").text

    # The Settings form posts every field, so a ticked box is enough to restore it.
    client.post("/settings", data={"show_get_started": "1"})

    assert 'id="get-started" open' in client.get("/").text


# --- the navigation -----------------------------------------------------------


def test_the_navigation_is_down_to_three_entries(client):
    page = client.get("/").text
    assert 'href="/"' in page
    assert 'href="/profiler"' in page
    assert 'href="/settings"' in page
    assert 'href="/tracker"' not in page
    assert 'href="/ranking"' not in page
    assert 'href="/templates"' not in page


def test_the_old_pages_redirect_to_the_board(client):
    for path in ("/offers", "/applications", "/tracker"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/"

    ranking = client.get("/ranking", follow_redirects=False)
    assert ranking.status_code == 303
    assert ranking.headers["location"] == "/#ranking"  # reopens the modal

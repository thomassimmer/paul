from __future__ import annotations

from app.config import Settings, Wish, load_settings, save_settings
from app.models import AxisVerdict, Elimination, Identity, Offer, OfferDraft, Profile, ScoringGrid
from app.offers import store as offers_store
from app.profiler import store as profile_store
from app.ranking import jobs, score, store

MODEL = "openai/gpt-4o"


def _seed_profile() -> None:
    profile_store.save_profile(Profile(identity=Identity(name="Camille Moreau")))


def _seed_offer(title: str = "Senior Backend Engineer") -> int:
    offer: Offer = OfferDraft(title=title, company="Acme").to_offer([])
    return offers_store.save_offer(offer, raw="", cleaned="cleaned text", source="text").id


def _configure(**overrides) -> None:
    base = {
        "model": MODEL,
        "filter_rules": "Eliminate offers requiring a security clearance.",
        "wishes": [Wish(label="remote", weight=2.0)],
    }
    base.update(overrides)
    save_settings(Settings(**base))


def _patch(monkeypatch, *, eliminated: bool = False, total: int = 74) -> None:
    async def fake_eliminate(settings, *, offer, profile, rules):
        if eliminated:
            return Elimination(
                eliminated=True,
                rule="Eliminate offers requiring a security clearance.",
                excerpt="Candidates must hold an active SC clearance.",
            )
        return Elimination()

    async def fake_score(settings, *, offer, profile, wishes):
        grid = ScoringGrid(
            technical_match=AxisVerdict(score=4, justification="Rust matches the must-haves")
        )
        result = score.build_score(grid)
        result.total = total
        return result

    monkeypatch.setattr("app.ranking.eliminate.eliminate_offer", fake_eliminate)
    monkeypatch.setattr("app.ranking.score.score_offer", fake_score)


def _run_inline(monkeypatch) -> None:
    """Replace the background scheduling with an inline run, so tests are deterministic."""

    async def inline(settings, profile, records, rankings, *, scope, concurrency):
        job = jobs.remember(jobs.build_job(records, scope=scope, concurrency=concurrency))
        assert job is not None
        await jobs.run(job, settings, profile, records, rankings)
        return job

    monkeypatch.setattr("app.ranking.jobs.start_job", inline)


def _running_job() -> jobs.Job:
    job = jobs.Job(
        scope="all", concurrency=2, offers=[jobs.JobOffer(offer_id=1, title="A", company="B")]
    )
    jobs.remember(job)
    return job


# --- the page -----------------------------------------------------------------


def test_ranking_page_renders_without_a_profile(client):
    response = client.get("/ranking")
    assert response.status_code == 200
    assert "Import your CV" in response.text


def test_ranking_page_renders_the_criteria_and_the_pace(client):
    _seed_offer()  # the run form only appears when there is something to rank
    response = client.get("/ranking")
    assert response.status_code == 200
    assert 'name="filter_rules"' in response.text
    assert 'name="wishes"' in response.text
    assert 'name="concurrency"' in response.text
    assert 'name="scope"' in response.text


def test_offers_appear_as_not_ranked(client):
    _seed_offer()
    assert "not ranked" in client.get("/ranking").text


def test_criteria_and_pace_are_saved(client):
    response = client.post(
        "/ranking/rules",
        data={
            "filter_rules": "Eliminate clearance roles.",
            "wishes": "startup: 3\nremote",
            "concurrency": "2",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    settings = load_settings()
    assert settings.filter_rules == "Eliminate clearance roles."
    assert [(wish.label, wish.weight) for wish in settings.wishes] == [("startup", 3.0), ("remote", 1.0)]
    assert settings.ranking_concurrency == 2


def test_an_absurd_concurrency_is_clamped(client):
    client.post("/ranking/rules", data={"filter_rules": "", "wishes": "", "concurrency": "500"})
    assert load_settings().ranking_concurrency == 8


# --- running ------------------------------------------------------------------


def test_running_shows_scores_and_reasons(client, monkeypatch):
    _seed_profile()
    _seed_offer()
    _configure()
    _patch(monkeypatch, total=74)
    _run_inline(monkeypatch)

    response = client.post("/ranking/run", follow_redirects=True)

    assert response.status_code == 200
    assert "74" in response.text
    assert "Why this score" in response.text
    assert "Rust matches the must-haves" in response.text
    assert "Kept" in response.text
    assert "Last run" in response.text  # the report panel


def test_running_shows_the_elimination_evidence(client, monkeypatch):
    _seed_profile()
    _seed_offer()
    _configure()
    _patch(monkeypatch, eliminated=True)
    _run_inline(monkeypatch)

    response = client.post("/ranking/run", follow_redirects=True)

    assert "Eliminated" in response.text
    assert "Eliminate offers requiring a security clearance." in response.text
    assert "Candidates must hold an active SC clearance." in response.text


def test_nothing_to_do_when_everything_is_up_to_date(client, monkeypatch):
    _seed_profile()
    _seed_offer()
    _configure()
    _patch(monkeypatch)
    _run_inline(monkeypatch)
    client.post("/ranking/run")

    response = client.post("/ranking/run", follow_redirects=True)

    assert "Nothing to do" in response.text


def test_all_scope_re_ranks_up_to_date_offers(client, monkeypatch):
    _seed_profile()
    _seed_offer()
    _configure()
    _patch(monkeypatch)
    _run_inline(monkeypatch)
    client.post("/ranking/run")

    response = client.post("/ranking/run", data={"scope": "all"}, follow_redirects=True)

    assert "1 offer(s) ranked" in response.text or "in the background" in response.text


def test_selected_scope_needs_a_tick(client, monkeypatch):
    _seed_profile()
    _seed_offer()
    _configure()
    _patch(monkeypatch)
    _run_inline(monkeypatch)

    response = client.post(
        "/ranking/run", data={"scope": "selected"}, follow_redirects=True
    )

    assert "No offer was ticked" in response.text


def test_selected_scope_ranks_only_the_ticked_offers(client, monkeypatch):
    _seed_profile()
    first = _seed_offer("Senior Backend Engineer")
    second = _seed_offer("Data Engineer")
    _configure()
    _patch(monkeypatch)
    _run_inline(monkeypatch)

    response = client.post(
        "/ranking/run",
        data={"scope": "selected", "offer_ids": str(second)},
        follow_redirects=True,
    )

    assert "1 offer(s)" in response.text
    assert store.load_ranking(second) is not None
    assert store.load_ranking(first) is None


def test_a_changed_criterion_marks_the_score_out_of_date(client, monkeypatch):
    _seed_profile()
    _seed_offer()
    _configure()
    _patch(monkeypatch)
    _run_inline(monkeypatch)
    client.post("/ranking/run")

    assert 'class="tag tag-warn">out of date' not in client.get("/ranking").text

    client.post("/ranking/rules", data={"filter_rules": "", "wishes": "startup: 5", "concurrency": "4"})

    assert 'class="tag tag-warn">out of date' in client.get("/ranking").text


def test_running_needs_a_profile(client, monkeypatch):
    _seed_offer()
    _configure()
    _patch(monkeypatch)
    _run_inline(monkeypatch)

    response = client.post("/ranking/run", follow_redirects=True)

    assert "Import your CV first" in response.text


def test_running_needs_a_model(client):
    _seed_profile()
    _seed_offer()
    save_settings(Settings())
    response = client.post("/ranking/run", follow_redirects=True)
    assert "No model configured" in response.text


def test_a_second_run_is_refused_while_one_is_running(client):
    _seed_profile()
    _seed_offer()
    _configure()
    _running_job()

    response = client.post("/ranking/run", follow_redirects=True)

    assert "already running" in response.text


# --- progress, stopping, dismissing -------------------------------------------


def test_the_progress_endpoint_returns_the_panel_and_the_table(client, monkeypatch):
    _seed_profile()
    _seed_offer()
    _configure()
    _patch(monkeypatch)
    _run_inline(monkeypatch)
    client.post("/ranking/run")

    response = client.get("/ranking/progress")

    assert response.status_code == 200
    assert 'id="job"' in response.text
    assert 'hx-swap-oob="outerHTML"' in response.text
    assert "Senior Backend Engineer" in response.text


def test_a_running_job_polls_itself(client):
    _seed_profile()
    _running_job()

    response = client.get("/ranking")

    assert 'hx-trigger="every 2s"' in response.text
    assert "Ranking in progress" in response.text


def test_a_finished_job_stops_polling(client, monkeypatch):
    _seed_profile()
    _seed_offer()
    _configure()
    _patch(monkeypatch)
    _run_inline(monkeypatch)
    client.post("/ranking/run")

    response = client.get("/ranking")

    assert "Last run" in response.text
    assert 'hx-trigger="every 2s"' not in response.text


def test_stopping_a_job(client):
    _seed_profile()
    _running_job()

    response = client.post("/ranking/cancel", follow_redirects=True)
    assert "Stopping" in response.text

    jobs.reset()
    response = client.post("/ranking/cancel", follow_redirects=True)
    assert "Nothing is running" in response.text


def test_dismissing_the_report(client):
    _seed_profile()
    _running_job()
    assert jobs.current() is not None

    response = client.post("/ranking/dismiss", follow_redirects=True)

    assert jobs.current() is None
    assert "Ranking in progress" not in response.text


# --- manual overrides ---------------------------------------------------------


def test_a_scored_offer_can_be_eliminated_and_restored_by_hand(client, monkeypatch):
    _seed_profile()
    offer_id = _seed_offer()
    _configure()
    _patch(monkeypatch, total=74)
    _run_inline(monkeypatch)
    client.post("/ranking/run")

    response = client.post(f"/ranking/{offer_id}/eliminate", follow_redirects=True)
    assert "eliminated by hand" in response.text
    assert "Eliminated" in response.text

    response = client.post(f"/ranking/{offer_id}/keep", follow_redirects=True)
    assert "Offer restored" in response.text
    # Restored, but its score is gone: it has to be scored again.
    assert "needs scoring" in response.text

    response = client.post(f"/ranking/{offer_id}/reset", follow_redirects=True)
    # The rendered message is HTML-escaped, so assert on the part without an apostrophe.
    assert "back to the rules" in response.text


def test_overriding_a_missing_offer_is_reported(client):
    response = client.post("/ranking/999/keep", follow_redirects=True)
    assert "no longer exists" in response.text

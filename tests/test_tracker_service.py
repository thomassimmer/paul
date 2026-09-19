from __future__ import annotations

from datetime import date

from app.config import Settings
from app.models import Application, Elimination, OfferDraft, RankingRecord, Score, ScoringGrid
from app.offers import store as offers_store
from app.ranking import score
from app.tracker import service

TODAY = date(2026, 9, 19)


def _score_with(total: int) -> Score:
    result = score.build_score(ScoringGrid())
    result.total = total
    return result


def _offer(title: str = "An offer", *, analyzed_at: str = "2026-09-01 08:00:00"):
    offer = OfferDraft(title=title, company="Acme").to_offer([])
    record = offers_store.save_offer(offer, raw="", cleaned="", source="text")
    record.analyzed_at = analyzed_at
    return record


# --- statuses -----------------------------------------------------------------


def test_statuses_follow_the_readme_flow():
    assert service.STATUS_ORDER == [
        "analyzed",
        "shortlisted",
        "ready",
        "applied",
        "interview",
        "offer",
        "rejected",
        "no_response",
    ]
    assert service.STATUS_LABELS["no_response"] == "No response"


def test_an_unknown_status_falls_back_to_the_default():
    assert service.normalize_status(" Applied ") == "applied"
    assert service.normalize_status("nonsense") == "analyzed"
    assert service.normalize_status("") == "analyzed"


def test_has_applied_covers_everything_after_sending():
    assert service.has_applied("analyzed") is False
    assert service.has_applied("ready") is False
    for status in ("applied", "interview", "offer", "rejected", "no_response"):
        assert service.has_applied(status) is True


# --- dates and follow-ups -----------------------------------------------------


def test_days_since_handles_blank_and_invalid_dates():
    assert service.days_since("", TODAY) is None
    assert service.days_since("not a date", TODAY) is None
    assert service.days_since("2026-09-09", TODAY) == 10


def test_a_followup_is_only_due_after_applying():
    settings = Settings(followup_days=7)
    fresh = Application(offer_id=1, status="analyzed")
    assert service.followup(fresh, settings.followup_days, TODAY) == (False, None)

    applied = Application(offer_id=1, status="applied", applied_on="2026-09-15")
    assert service.followup(applied, settings.followup_days, TODAY) == (False, 4)

    stale = Application(offer_id=1, status="applied", applied_on="2026-09-01")
    assert service.followup(stale, settings.followup_days, TODAY) == (True, 18)


def test_the_clock_counts_from_the_last_contact():
    settings = Settings(followup_days=7)
    application = Application(
        offer_id=1, status="applied", applied_on="2026-08-01", last_contact="2026-09-18"
    )
    assert service.followup(application, settings.followup_days, TODAY) == (False, 1)


def test_an_applied_status_without_a_date_is_not_flagged():
    application = Application(offer_id=1, status="applied")
    assert service.followup(application, 7, TODAY) == (False, None)


def test_the_delay_comes_from_the_settings():
    application = Application(offer_id=1, status="applied", applied_on="2026-09-15")
    assert service.followup(application, 30, TODAY)[0] is False
    assert service.followup(application, 3, TODAY)[0] is True


def test_applying_today_is_recorded_automatically():
    applied_on, last_contact = service.next_dates(
        "applied", Application(offer_id=1), TODAY
    )
    assert applied_on == "2026-09-19"
    assert last_contact == ""


def test_moving_on_does_not_overwrite_known_dates():
    existing = Application(offer_id=1, applied_on="2026-09-01", last_contact="2026-09-10")
    assert service.next_dates("interview", existing, TODAY) == ("2026-09-01", "2026-09-10")
    assert service.next_dates("ready", existing, TODAY) == ("2026-09-01", "2026-09-10")


# --- the board ----------------------------------------------------------------


def _rows():
    first = _offer("Senior Backend Engineer")
    second = _offer("Data Engineer")
    offers = [first, second]
    rankings = {
        first.id: _ranking(first.id, _score_with(90)),
        second.id: _ranking(second.id, _score_with(40)),
    }
    applications = {
        second.id: Application(offer_id=second.id, status="applied", applied_on="2026-09-01"),
    }
    return service.tracker_rows(offers, rankings, applications, Settings(), TODAY)


def _ranking(offer_id: int, value: Score) -> RankingRecord:
    return RankingRecord(offer_id=offer_id, elimination=Elimination(), score=value)


def test_rows_carry_status_score_dates_and_followup():
    rows = {row["offer"].offer.title: row for row in _rows()}

    senior = rows["Senior Backend Engineer"]
    assert senior["status"] == "analyzed"  # no row means the default
    assert senior["total"] == 90
    assert senior["analyzed_on"] == "2026-09-01"
    assert senior["followup_due"] is False
    assert senior["applied"] is False

    data = rows["Data Engineer"]
    assert data["status"] == "applied"
    assert data["applied_on"] == "2026-09-01"
    assert data["followup_due"] is True
    assert data["days_since_contact"] == 18


def test_filter_rows():
    rows = _rows()
    assert len(service.filter_rows(rows, "all")) == 2
    assert [row["offer"].offer.title for row in service.filter_rows(rows, "applied")] == ["Data Engineer"]
    assert [row["offer"].offer.title for row in service.filter_rows(rows, "not_applied")] == [
        "Senior Backend Engineer"
    ]
    assert [row["offer"].offer.title for row in service.filter_rows(rows, "followup")] == ["Data Engineer"]
    assert service.filter_rows(rows, "nonsense") == rows  # falls back to "all"


def test_sort_rows():
    rows = _rows()
    by_score = service.sort_rows(rows, "score", "desc")
    assert [row["total"] for row in by_score] == [90, 40]
    by_score_asc = service.sort_rows(rows, "score", "asc")
    assert [row["total"] for row in by_score_asc] == [40, 90]

    by_role = service.sort_rows(rows, "role", "asc")
    assert [row["offer"].offer.title for row in by_role] == ["Data Engineer", "Senior Backend Engineer"]

    by_status = service.sort_rows(rows, "status", "asc")
    assert [row["status"] for row in by_status] == ["analyzed", "applied"]

    # An unknown key falls back to the default instead of raising.
    assert service.sort_rows(rows, "drop table", "desc")[0]["total"] == 90


def test_board_summary():
    summary = service.board_summary(_rows())
    assert summary == {"total": 2, "due": 1, "applied": 1, "not_applied": 1}

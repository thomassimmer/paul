from __future__ import annotations

from datetime import date

from app.config import Settings
from app.models import Application
from app.tracker import service

TODAY = date(2026, 9, 19)


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

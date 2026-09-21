"""Tracker rules, computed by code.

Statuses are changed by hand; everything else is derived: which statuses mean "I
have applied", how long it has been since the last news, and whether that is long
enough to warrant a follow-up. Dates are compared as dates, so a follow-up does
not depend on what time of day you open the page.

The rows themselves are built in ``app/web/board.py``: a row is a view of four
features at once, so it belongs with the page that shows it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.models import Application

# The order is the one from the README, and it decides how the board sorts.
STATUS_LABELS: dict[str, str] = {
    "analyzed": "Analyzed",
    "shortlisted": "Shortlisted",
    "ready": "Ready",
    "applied": "Applied",
    "interview": "Interview",
    "offer": "Offer",
    "rejected": "Rejected",
    "no_response": "No response",
}
STATUS_ORDER: list[str] = list(STATUS_LABELS)
DEFAULT_STATUS = "analyzed"

# Statuses that mean an application was sent: from there, silence is information.
APPLIED_STATUSES = {"applied", "interview", "offer", "rejected", "no_response"}
# Only "applied" waits for news: the others either got an answer or are over.
FOLLOWUP_STATUSES = {"applied"}


def normalize_status(value: str) -> str:
    """Accept a status from a form, and fall back to the default when unknown."""
    status = (value or "").strip().lower()
    return status if status in STATUS_LABELS else DEFAULT_STATUS


def has_applied(status: str) -> bool:
    return normalize_status(status) in APPLIED_STATUSES


def parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat((value or "").strip()[:10])
    except ValueError:
        return None


def days_since(value: str, today: date) -> int | None:
    parsed = parse_date(value)
    if parsed is None:
        return None
    return (today - parsed).days


def followup(application: Application, followup_days: int, today: date) -> tuple[bool, int | None]:
    """``(due, days)`` since the last news, when the status warrants a follow-up.

    A later contact resets the clock: the point is how long *silence* has lasted.
    """
    if normalize_status(application.status) not in FOLLOWUP_STATUSES:
        return False, None
    reference = application.last_contact or application.applied_on
    elapsed = days_since(reference, today)
    if elapsed is None:
        return False, None
    return elapsed >= followup_days, elapsed


def next_dates(status: str, application: Application, today: date) -> tuple[str, str]:
    """What ``applied_on`` / ``last_contact`` should become when the status is set.

    Applying today records today's date, so the follow-up clock starts without the
    user having to type anything; the other transitions never overwrite a date
    they were not given.
    """
    applied_on, last_contact = application.applied_on, application.last_contact
    if normalize_status(status) in APPLIED_STATUSES and not applied_on:
        applied_on = today.isoformat()
    return applied_on, last_contact


def today_utc() -> date:
    return datetime.now(UTC).date()

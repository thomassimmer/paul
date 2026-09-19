"""Tracker rules, computed by code.

Statuses are changed by hand in the MVP; everything else on the page is derived:
which statuses mean "I have applied", how long it has been since the last news,
and whether that is long enough to warrant a follow-up. Dates are compared as
dates, so a follow-up does not depend on what time of day you open the page.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.config import Settings
from app.models import Application, OfferRecord, RankingRecord

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

SORT_KEYS = ("role", "company", "score", "status", "applied", "followup")
DEFAULT_SORT = "score"
DEFAULT_DIRECTION = "desc"

FILTERS = {
    "all": "Everything",
    "followup": "Follow-up due",
    "not_applied": "Not applied to yet",
    **{slug: label for slug, label in STATUS_LABELS.items()},
}
DEFAULT_FILTER = "all"


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


def tracker_rows(
    offers: list[OfferRecord],
    rankings: dict[int, RankingRecord],
    applications: dict[int, Application],
    settings: Settings,
    today: date,
) -> list[dict]:
    """One row per offer, with its status, its score and its follow-up state."""
    rows: list[dict] = []
    for offer in offers:
        application = applications.get(offer.id) or Application(offer_id=offer.id)
        ranking = rankings.get(offer.id)
        due, elapsed = followup(application, settings.followup_days, today)
        rows.append(
            {
                "offer": offer,
                "ranking": ranking,
                "total": (
                    ranking.score.total
                    if ranking is not None and ranking.score is not None
                    else None
                ),
                "eliminated": ranking.eliminated if ranking is not None else False,
                "status": normalize_status(application.status),
                "status_label": STATUS_LABELS[normalize_status(application.status)],
                "applied_on": application.applied_on,
                "last_contact": application.last_contact,
                "analyzed_on": (offer.analyzed_at or "")[:10],
                "notes": application.notes,
                "followup_due": due,
                "days_since_contact": elapsed,
                "applied": has_applied(application.status),
            }
        )
    return rows


def board_summary(rows: list[dict]) -> dict[str, int]:
    """The counters shown above the board and on the dashboard."""
    return {
        "total": len(rows),
        "due": sum(1 for row in rows if row["followup_due"]),
        "applied": sum(1 for row in rows if row["applied"]),
        "not_applied": sum(1 for row in rows if not row["applied"]),
    }


def filter_rows(rows: list[dict], wanted: str) -> list[dict]:
    key = wanted if wanted in FILTERS else DEFAULT_FILTER
    if key == "all":
        return rows
    if key == "followup":
        return [row for row in rows if row["followup_due"]]
    if key == "not_applied":
        return [row for row in rows if not row["applied"]]
    return [row for row in rows if row["status"] == key]


def _sort_value(row: dict, key: str):
    if key == "role":
        return (row["offer"].offer.title or "").casefold()
    if key == "company":
        return (row["offer"].offer.company or "").casefold()
    if key == "score":
        return row["total"] if row["total"] is not None else -1
    if key == "status":
        return STATUS_ORDER.index(row["status"])
    if key == "applied":
        return row["applied_on"] or ""
    if key == "followup":
        return row["days_since_contact"] if row["days_since_contact"] is not None else -1
    return row["total"] if row["total"] is not None else -1


def sort_rows(rows: list[dict], key: str, direction: str) -> list[dict]:
    """Sort on a whitelisted key: ``sort`` and ``dir`` come from the query string."""
    key = key if key in SORT_KEYS else DEFAULT_SORT
    reverse = (direction or DEFAULT_DIRECTION).lower() != "asc"
    return sorted(rows, key=lambda row: _sort_value(row, key), reverse=reverse)


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
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).date()


def days_ago(value: int) -> str:
    """An ISO date ``value`` days in the past; handy for tests and defaults."""
    return (today_utc() - timedelta(days=value)).isoformat()

"""The offers board: one row per offer, and the only place that builds them.

Offers, ranking, applications and tracking each hold a part of one offer's story.
The board is where the four are put side by side, so the row is built once here
instead of once per page — it used to be three near-identical builders, and they
had already started to drift. Filtering and sorting live here too: they are
exactly what the board's controls do, and they only make sense on these rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.config import Settings
from app.models import Application, OfferRecord, Profile, RankingRecord
from app.ranking import service as ranking_service
from app.tracker import service as tracker_service
from app.writer import store as writer_store

SORT_KEYS = ("role", "score", "status", "applied", "followup")
DEFAULT_SORT = "score"
DEFAULT_DIRECTION = "desc"

FILTERS = {
    "all": "Everything",
    "followup": "Follow-up due",
    "not_applied": "Not applied to yet",
    **tracker_service.STATUS_LABELS,
}
DEFAULT_FILTER = "all"


@dataclass(frozen=True)
class Row:
    """One offer, with its verdict, its documents and where it stands.

    Every field is resolved here rather than in the template: a template should
    read a row, not work out what a row means.
    """

    record: OfferRecord
    ranking: RankingRecord | None
    application: Application | None

    total: int | None
    eliminated: bool
    needs_score: bool
    stale: bool

    status: str
    status_label: str
    applied_on: str
    notes: str
    prepared: bool
    folder: str
    followup_due: bool
    days_since_contact: int | None
    applied: bool

    @property
    def title(self) -> str:
        return self.record.offer.title or "Untitled offer"

    @property
    def company(self) -> str:
        return self.record.offer.company or ""


def build_rows(
    offers: list[OfferRecord],
    rankings: dict[int, RankingRecord],
    applications: dict[int, Application],
    settings: Settings,
    profile: Profile,
    today: date,
) -> list[Row]:
    """One row per offer, in the order the offers were analyzed."""
    return [
        _row(
            record,
            rankings.get(record.id),
            applications.get(record.id),
            settings,
            profile,
            today,
        )
        for record in offers
    ]


def _row(
    record: OfferRecord,
    ranking: RankingRecord | None,
    application: Application | None,
    settings: Settings,
    profile: Profile,
    today: date,
) -> Row:
    status = tracker_service.normalize_status(application.status if application else "")
    folder = application.folder if application else ""
    # A missing row is the default status, not missing data: this is where that
    # rule is turned into a row.
    due, elapsed = tracker_service.followup(
        application or Application(offer_id=record.id), settings.followup_days, today
    )
    score = ranking.score if ranking is not None else None
    return Row(
        record=record,
        ranking=ranking,
        application=application,
        total=score.total if score is not None else None,
        eliminated=ranking.eliminated if ranking is not None else False,
        needs_score=bool(
            ranking is not None and not ranking.eliminated and ranking.score is None
        ),
        stale=ranking_service.is_stale(settings, profile, record.offer, ranking),
        status=status,
        status_label=tracker_service.STATUS_LABELS[status],
        applied_on=application.applied_on if application else "",
        notes=application.notes if application else "",
        prepared=bool(folder) and writer_store.folder_exists(folder),
        folder=folder,
        followup_due=due,
        days_since_contact=elapsed,
        applied=tracker_service.has_applied(status),
    )


def summary(rows: list[Row]) -> dict[str, int]:
    """The counters shown above the table and in "Get started"."""
    return {
        "total": len(rows),
        "due": sum(1 for row in rows if row.followup_due),
        "applied": sum(1 for row in rows if row.applied),
        "not_applied": sum(1 for row in rows if not row.applied),
    }


def filter_rows(rows: list[Row], wanted: str) -> list[Row]:
    key = wanted if wanted in FILTERS else DEFAULT_FILTER
    if key == "all":
        return rows
    if key == "followup":
        return [row for row in rows if row.followup_due]
    if key == "not_applied":
        return [row for row in rows if not row.applied]
    return [row for row in rows if row.status == key]


def _sort_value(row: Row, key: str):
    if key == "role":
        return row.title.casefold()
    if key == "status":
        return tracker_service.STATUS_ORDER.index(row.status)
    if key == "applied":
        return row.applied_on or ""
    if key == "followup":
        return row.days_since_contact if row.days_since_contact is not None else -1
    # "score", and anything the caller got wrong. Eliminated offers come last
    # however they compare otherwise, and an unscored offer sorts below any score.
    return (not row.eliminated, row.total if row.total is not None else -1)


def sort_rows(rows: list[Row], key: str, direction: str) -> list[Row]:
    """Sort on a whitelisted key: ``sort`` and ``dir`` come from the query string."""
    key = key if key in SORT_KEYS else DEFAULT_SORT
    reverse = (direction or DEFAULT_DIRECTION).lower() != "asc"
    return sorted(rows, key=lambda row: _sort_value(row, key), reverse=reverse)

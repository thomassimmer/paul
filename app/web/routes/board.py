"""The board: every offer, and everything you have done about it.

This is the home page and the only list. The dashboard, the offers list, the
ranking page, the applications list and the tracker were five views of the same
join, so they are one table now. The two things that are genuinely a page of
their own — the ranking criteria and a run — live in a modal the table stays
visible behind, and the selection the run uses is the table's own checkboxes.

The long jobs (ranking, preparing) report through two panels that poll their own
endpoints; each answer also refreshes the table, out of band, so a finished
ranking shows its scores without a reload.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.config import format_wishes, load_settings
from app.models import Profile
from app.offers import store as offers_store
from app.profiler import service as profiler_service
from app.profiler import store as profile_store
from app.ranking import jobs as ranking_jobs
from app.ranking import service as ranking_service
from app.ranking import store as ranking_store
from app.tracker import service as tracker_service
from app.tracker import store as tracker_store
from app.web import board
from app.web.templating import render
from app.writer import jobs as writer_jobs

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _profile() -> tuple[Profile | None, str | None]:
    try:
        return profile_store.load_profile(), None
    except profile_store.ProfileError as exc:
        return None, str(exc)


def context(sort: str, direction: str, status: str) -> dict:
    """Everything the board renders, from one read of each store."""
    settings = load_settings()
    profile, profile_error = _profile()
    # Without a profile there is nothing to compare an offer to, but the board
    # still has to render: an empty profile simply ranks everything as "not
    # ranked" instead of hiding the table.
    usable = profile if profile is not None else Profile()

    offers = offers_store.list_offers()
    rankings = ranking_store.list_rankings()
    applications = tracker_store.list_applications()
    rows = board.build_rows(
        offers, rankings, applications, settings, usable, tracker_service.today_utc()
    )
    counts = board.summary(rows)
    pending = ranking_service.select_offers("pending", offers, rankings, settings, usable)
    filtered = board.filter_rows(rows, status)

    configured = bool(settings.model.strip())
    ranked = len(rankings)
    prepared = sum(1 for application in applications.values() if application.folder)
    return {
        "settings": settings,
        "has_profile": profile is not None,
        "profile_error": profile_error,
        "has_model": configured,
        "summary": profiler_service.summary(profile),
        "get_started": {
            "configured": configured,
            "offers": len(offers),
            "ranked": ranked,
            "prepared": prepared,
            "applied": counts["applied"],
            "done": bool(
                configured
                and profile is not None
                and offers
                and ranked
                and prepared
                and counts["applied"]
            ),
        },
        # The table, its filter and its order.
        "rows": board.sort_rows(filtered, sort, direction),
        "shown": len(filtered),
        "counts": counts,
        "filters": board.FILTERS,
        "status_filter": status if status in board.FILTERS else board.DEFAULT_FILTER,
        "sort": sort,
        "direction": direction,
        # What the status select in each row offers.
        "statuses": tracker_service.STATUS_LABELS,
        "status_order": tracker_service.STATUS_ORDER,
        "followup_days": settings.followup_days,
        # The ranking modal.
        "wishes_text": format_wishes(settings.wishes),
        "scopes": ranking_service.SCOPES,
        "default_scope": ranking_service.DEFAULT_SCOPE,
        "pending_count": len(pending),
        "up_to_date_count": len(offers) - len(pending),
        "total_count": len(offers),
        "ranking_job": ranking_jobs.current(),
        "ranking_status_labels": ranking_jobs.STATUS_LABELS,
        # The preparation job, and whether anything is running at all.
        "job": writer_jobs.current(),
        "status_labels": writer_jobs.STATUS_LABELS,
        "next_url": "/",
    }


def _busy() -> bool:
    ranking = ranking_jobs.current()
    writer = writer_jobs.current()
    return bool(
        (ranking is not None and ranking.running) or (writer is not None and writer.running)
    )


@router.get("/", response_class=HTMLResponse)
async def board_page(
    request: Request,
    sort: str = board.DEFAULT_SORT,
    dir: str = board.DEFAULT_DIRECTION,
    status: str = board.DEFAULT_FILTER,
):
    return render(
        request, "board/index.html", active="offers", busy=_busy(), **context(sort, dir, status)
    )


@router.get("/progress/ranking", response_class=HTMLResponse)
async def ranking_progress(
    request: Request,
    sort: str = board.DEFAULT_SORT,
    dir: str = board.DEFAULT_DIRECTION,
    status: str = board.DEFAULT_FILTER,
):
    """The ranking job panel, plus the refreshed table via an out-of-band swap."""
    return render(request, "board/partials/progress_ranking.html", **context(sort, dir, status))


@router.get("/progress/writing", response_class=HTMLResponse)
async def writing_progress(
    request: Request,
    sort: str = board.DEFAULT_SORT,
    dir: str = board.DEFAULT_DIRECTION,
    status: str = board.DEFAULT_FILTER,
):
    """The preparation job panel, plus the refreshed table."""
    return render(request, "board/partials/progress_writing.html", **context(sort, dir, status))

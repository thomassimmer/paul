"""Ranking routes: the criteria, a background run with progress, manual overrides.

The run itself is a background task (see ``jobs.py``), so these routes only
decide *what* to rank, then let the page poll ``/ranking/progress``.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.config import format_wishes, load_settings, parse_wishes, save_settings
from app.models import Profile
from app.offers import store as offers_store
from app.profiler import store as profile_store
from app.ranking import jobs, service, store
from app.tracker import store as tracker_store
from app.web.templating import redirect, render

router = APIRouter(prefix="/ranking", tags=["ranking"])

MAX_CONCURRENCY = 8

_OVERRIDE_MESSAGES = {
    "kept": "Offer restored: the rules no longer eliminate it.",
    "eliminated": "Offer eliminated by hand.",
    "": "Offer is back to the rules' verdict.",
}
_EMPTY_SCOPE_MESSAGES = {
    "selected": "No offer was ticked, so there was nothing to rank.",
    "pending": "Nothing to do: every offer is already ranked and up to date.",
    "all": "No offer to rank yet.",
    "not_applied": "Every offer already has an application, so there is nothing to rank.",
}


def _profile() -> tuple[Profile | None, str | None]:
    try:
        return profile_store.load_profile(), None
    except profile_store.ProfileError as exc:
        return None, str(exc)


def _context() -> dict:
    settings = load_settings()
    profile, profile_error = _profile()
    usable = profile if profile is not None else Profile()

    offers = offers_store.list_offers()
    rankings = store.list_rankings()
    pending = service.select_offers("pending", offers, rankings, settings, usable)

    return {
        "settings": settings,
        "wishes_text": format_wishes(settings.wishes),
        "rows": service.ranking_rows(offers, rankings, settings, usable),
        "scopes": service.SCOPES,
        "default_scope": service.DEFAULT_SCOPE,
        "pending_count": len(pending),
        "up_to_date_count": len(offers) - len(pending),
        "total_count": len(offers),
        "job": jobs.current(),
        "status_labels": jobs.STATUS_LABELS,
        "has_profile": profile is not None,
        "profile_error": profile_error,
    }


@router.get("", response_class=HTMLResponse)
async def ranking_page(request: Request):
    return render(request, "ranking/index.html", active="ranking", **_context())


@router.get("/progress", response_class=HTMLResponse)
async def ranking_progress(request: Request):
    """The job panel, plus the refreshed table via an out-of-band swap."""
    return render(request, "ranking/partials/progress.html", **_context())


@router.post("/rules")
async def ranking_rules(request: Request):
    form = await request.form()
    settings = load_settings()

    try:
        concurrency = int(str(form.get("concurrency") or settings.ranking_concurrency))
    except ValueError:
        concurrency = settings.ranking_concurrency
    concurrency = max(1, min(MAX_CONCURRENCY, concurrency))

    save_settings(
        settings.model_copy(
            update={
                "filter_rules": str(form.get("filter_rules") or "").strip(),
                "wishes": parse_wishes(str(form.get("wishes") or "")),
                "ranking_concurrency": concurrency,
            }
        )
    )
    return redirect("/ranking", message="Elimination rules, wishes and pace saved.")


@router.post("/run")
async def ranking_run(request: Request):
    form = await request.form()
    settings = load_settings()
    profile, error = _profile()

    if profile is None:
        return redirect(
            "/ranking",
            message=error or "Import your CV first: offers are scored against your profile.",
            level="warning",
        )
    if not settings.model.strip():
        return redirect(
            "/ranking",
            message="No model configured. Set one in Settings to rank your offers.",
            level="error",
        )

    running = jobs.current()
    if running is not None and running.running:
        return redirect("/ranking", message="A ranking is already running.", level="warning")

    scope = str(form.get("scope") or service.DEFAULT_SCOPE)
    if scope not in service.SCOPES:
        scope = service.DEFAULT_SCOPE
    selected_ids = [
        int(str(value)) for value in form.getlist("offer_ids") if str(value).isdigit()
    ]

    offers = offers_store.list_offers()
    rankings = store.list_rankings()
    records = service.select_offers(
        scope,
        offers,
        rankings,
        settings,
        profile,
        selected_ids,
        tracker_store.applied_offer_ids(),
    )

    if not records:
        return redirect(
            "/ranking", message=_EMPTY_SCOPE_MESSAGES[scope], level="warning"
        )

    await jobs.start_job(
        settings,
        profile,
        records,
        rankings,
        scope=scope,
        concurrency=settings.ranking_concurrency,
    )
    return redirect(
        "/ranking",
        message=f"Ranking {len(records)} offer(s) in the background. This page updates itself.",
    )


@router.post("/cancel")
async def ranking_cancel():
    if jobs.cancel():
        return redirect("/ranking", message="Stopping: the calls already in flight will finish.")
    return redirect("/ranking", message="Nothing is running.", level="warning")


@router.post("/dismiss")
async def ranking_dismiss():
    jobs.reset()
    return redirect("/ranking")


def _override(offer_id: int, override: str):
    if offers_store.load_offer(offer_id) is None:
        return redirect("/ranking", message="This offer no longer exists.", level="error")
    store.set_override(offer_id, override)
    return redirect("/ranking", message=_OVERRIDE_MESSAGES[override])


@router.post("/{offer_id}/keep")
async def ranking_keep(offer_id: int):
    return _override(offer_id, "kept")


@router.post("/{offer_id}/eliminate")
async def ranking_eliminate(offer_id: int):
    return _override(offer_id, "eliminated")


@router.post("/{offer_id}/reset")
async def ranking_reset(offer_id: int):
    return _override(offer_id, "")

"""Ranking actions: the criteria, a run, and the manual overrides.

The page itself is the board (``app/web/routes/board.py``): the criteria and the
run live in a modal there, and the run's progress is polled by the board. What
stays here is what *changes*: the rules, starting and stopping a run, and
overriding one verdict by hand.

A run is a background task (see ``jobs.py``), so these routes only decide what to
rank and redirect back to the board, which reopens the modal.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.config import load_settings, parse_wishes, save_settings
from app.models import Profile
from app.offers import store as offers_store
from app.profiler import store as profile_store
from app.ranking import jobs, service, store
from app.tracker import store as tracker_store
from app.web.templating import local_url, redirect

router = APIRouter(prefix="/ranking", tags=["ranking"])

MAX_CONCURRENCY = 8

# Where a ranking action lands when it is not given a ``next``: the board
# reopens the modal from the hash, so a run started in the dialog reports there.
BOARD = "/#ranking"

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


@router.get("")
async def ranking_page():
    """The criteria and the run live in the board's modal now."""
    return redirect(BOARD)


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
    return redirect(BOARD, message="Elimination rules, wishes and pace saved.")


@router.post("/run")
async def ranking_run(request: Request):
    form = await request.form()
    settings = load_settings()
    profile, error = _profile()

    if profile is None:
        return redirect(
            BOARD,
            message=error or "Import your CV first: offers are scored against your profile.",
            level="warning",
        )
    if not settings.model.strip():
        return redirect(
            BOARD,
            message="No model configured. Set one in Settings to rank your offers.",
            level="error",
        )

    running = jobs.current()
    if running is not None and running.running:
        return redirect(BOARD, message="A ranking is already running.", level="warning")

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
        return redirect(BOARD, message=_EMPTY_SCOPE_MESSAGES[scope], level="warning")

    await jobs.start_job(
        settings,
        profile,
        records,
        rankings,
        scope=scope,
        concurrency=settings.ranking_concurrency,
    )
    return redirect(
        BOARD,
        message=f"Ranking {len(records)} offer(s) in the background. The page updates itself.",
    )


@router.post("/cancel")
async def ranking_cancel():
    if jobs.cancel():
        return redirect(BOARD, message="Stopping: the calls already in flight will finish.")
    return redirect(BOARD, message="Nothing is running.", level="warning")


@router.post("/dismiss")
async def ranking_dismiss():
    jobs.reset()
    return redirect(BOARD)


async def _override(request: Request, offer_id: int, override: str):
    form = await request.form()
    back = local_url(form.get("next"), BOARD)
    if offers_store.load_offer(offer_id) is None:
        return redirect(back, message="This offer no longer exists.", level="error")
    store.set_override(offer_id, override)
    return redirect(back, message=_OVERRIDE_MESSAGES[override])


@router.post("/{offer_id}/keep")
async def ranking_keep(offer_id: int, request: Request):
    return await _override(request, offer_id, "kept")


@router.post("/{offer_id}/eliminate")
async def ranking_eliminate(offer_id: int, request: Request):
    return await _override(request, offer_id, "eliminated")


@router.post("/{offer_id}/reset")
async def ranking_reset(offer_id: int, request: Request):
    return await _override(request, offer_id, "")

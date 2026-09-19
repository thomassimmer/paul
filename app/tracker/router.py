"""Tracker routes: the board, a quick status change, and one application's detail."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.config import load_settings
from app.offers import store as offers_store
from app.ranking import store as ranking_store
from app.tracker import service, store
from app.web.templating import redirect, render

router = APIRouter(prefix="/tracker", tags=["tracker"])


def _list_url(form) -> str:
    """Keep the sort and the filter when coming back to the board.

    The row form carries the filter as ``filter``, not ``status``: ``status`` is
    the select that holds the new status, and one field name cannot serve both.
    """
    query = {
        "status": str(form.get("filter") or ""),
        "sort": str(form.get("sort") or ""),
        "dir": str(form.get("dir") or ""),
    }
    query = {key: value for key, value in query.items() if value}
    return "/tracker?" + urlencode(query) if query else "/tracker"


def _context() -> dict:
    settings = load_settings()
    rows = service.tracker_rows(
        offers_store.list_offers(),
        ranking_store.list_rankings(),
        store.list_applications(),
        settings,
        service.today_utc(),
    )
    return {
        "settings": settings,
        "rows": rows,
        "statuses": service.STATUS_LABELS,
        "status_order": service.STATUS_ORDER,
        "filters": service.FILTERS,
        "sort_keys": service.SORT_KEYS,
        "followup_days": settings.followup_days,
        "summary": service.board_summary(rows),
    }


@router.get("", response_class=HTMLResponse)
async def tracker_page(
    request: Request,
    sort: str = service.DEFAULT_SORT,
    dir: str = service.DEFAULT_DIRECTION,
    status: str = service.DEFAULT_FILTER,
):
    context = _context()
    rows = context.pop("rows")
    filtered = service.filter_rows(rows, status)
    return render(
        request,
        "tracker/index.html",
        active="tracker",
        sort=sort,
        direction=dir,
        status_filter=status,
        shown=len(filtered),
        rows=service.sort_rows(filtered, sort, dir),
        **context,
    )


@router.post("/{offer_id}/status")
async def tracker_set_status(offer_id: int, request: Request):
    form = await request.form()
    if offers_store.load_offer(offer_id) is None:
        return redirect("/tracker", message="This offer no longer exists.", level="error")
    status = service.normalize_status(str(form.get("status") or ""))
    store.set_status(offer_id, status, today=service.today_utc())
    message = f"Status set to {service.STATUS_LABELS[status]}."
    if status == "applied":
        message += " Applied today; the follow-up clock starts now."
    return redirect(_list_url(form), message=message)


@router.get("/{offer_id}", response_class=HTMLResponse)
async def tracker_detail(request: Request, offer_id: int):
    record = offers_store.load_offer(offer_id)
    if record is None:
        return redirect("/tracker", message="This offer no longer exists.", level="error")
    application = store.load_application(offer_id)
    return render(
        request,
        "tracker/detail.html",
        active="tracker",
        record=record,
        offer=record.offer,
        ranking=ranking_store.load_ranking(offer_id),
        application=application,
        status=application.status if application else service.DEFAULT_STATUS,
        statuses=service.STATUS_LABELS,
        followup_days=load_settings().followup_days,
    )


@router.post("/{offer_id}")
async def tracker_save(offer_id: int, request: Request):
    if offers_store.load_offer(offer_id) is None:
        return redirect("/tracker", message="This offer no longer exists.", level="error")
    form = await request.form()

    status = service.normalize_status(str(form.get("status") or ""))
    applied_on = str(form.get("applied_on") or "").strip()
    last_contact = str(form.get("last_contact") or "").strip()
    for label, value in (("applied", applied_on), ("last contact", last_contact)):
        if value and service.parse_date(value) is None:
            return redirect(
                f"/tracker/{offer_id}",
                message=f"The {label} date must look like 2026-09-19.",
                level="error",
            )

    store.save_application(
        offer_id,
        status=status,
        applied_on=applied_on,
        last_contact=last_contact,
        notes=str(form.get("notes") or "").strip(),
    )
    return redirect(f"/tracker/{offer_id}", message="Application saved.")


@router.post("/{offer_id}/clear")
async def tracker_clear(offer_id: int):
    """Forget the row: the offer goes back to the default status."""
    store.delete_application(offer_id)
    return redirect("/tracker", message="Application reset to Analyzed.")

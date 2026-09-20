"""Tracker actions: change a status, save one application, reset it.

The board renders the status selects and the offer page renders the form; what
lives here is what changes them. ``/tracker/{id}`` as a page is gone: the
tracking form is a section of the offer page, and the two old URLs redirect.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Request

from app.offers import store as offers_store
from app.tracker import service, store
from app.web.templating import local_url, redirect

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
    return "/?" + urlencode(query) if query else "/"


@router.get("")
async def tracker_page():
    """The board is the tracker now; keep the old link working."""
    return redirect("/")


@router.post("/{offer_id}/status")
async def tracker_set_status(offer_id: int, request: Request):
    form = await request.form()
    if offers_store.load_offer(offer_id) is None:
        return redirect("/", message="This offer no longer exists.", level="error")
    status = service.normalize_status(str(form.get("status") or ""))
    store.set_status(offer_id, status, today=service.today_utc())
    message = f"Status set to {service.STATUS_LABELS[status]}."
    if status == "applied":
        message += " Applied today; the follow-up clock starts now."
    return redirect(_list_url(form), message=message)


@router.get("/{offer_id}")
async def tracker_detail(offer_id: int):
    """The tracking form is on the offer page now; keep the old link working."""
    return redirect(f"/offers/{offer_id}#tracking")


@router.post("/{offer_id}")
async def tracker_save(offer_id: int, request: Request):
    if offers_store.load_offer(offer_id) is None:
        return redirect("/", message="This offer no longer exists.", level="error")
    form = await request.form()
    back = local_url(form.get("next"), f"/offers/{offer_id}#tracking")

    status = service.normalize_status(str(form.get("status") or ""))
    applied_on = str(form.get("applied_on") or "").strip()
    last_contact = str(form.get("last_contact") or "").strip()
    for label, value in (("applied", applied_on), ("last contact", last_contact)):
        if value and service.parse_date(value) is None:
            return redirect(
                back,
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
    return redirect(back, message="Application saved.")


@router.post("/{offer_id}/clear")
async def tracker_clear(offer_id: int, request: Request):
    """Forget the row: the offer goes back to the default status."""
    form = await request.form()
    back = local_url(form.get("next"), f"/offers/{offer_id}#tracking")
    store.delete_application(offer_id)
    return redirect(back, message="Application reset to Analyzed.")

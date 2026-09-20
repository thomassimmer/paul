"""Offer analyzer routes: paste a fragment, read the analysis, complete it.

The offer page is the busiest one in the app: it shows the offer itself, its
verdict, the tailored documents and the tracking form, because all four are about
the same offer and splitting them only made the user hop between pages. Routes
stay thin; the composition lives in ``_offer_context`` and in the feature
packages this page borrows from.
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.config import load_settings
from app.llm import LLMError
from app.models import OfferRecord, Profile
from app.offers import clean, editor, service, store
from app.profiler import store as profile_store
from app.ranking import store as ranking_store
from app.tracker import service as tracker_service
from app.tracker import store as tracker_store
from app.web.templating import redirect, render
from app.writer import jobs as writer_jobs
from app.writer import store as writer_store
from app.writer import view as writer_view

router = APIRouter(prefix="/offers", tags=["offers"])


def _number(form, key: str) -> int:
    try:
        return int(str(form.get(key, "")).strip())
    except (TypeError, ValueError):
        return 0


def _slots(fragments: Sequence[str]) -> list[str]:
    """Filled slots, plus the always-available blank one."""
    slots = list(fragments)
    while slots and not slots[-1].strip():
        slots.pop()
    return [*slots, ""]


def _load(offer_id: int):
    """Return ``(record, redirect_response)`` for routes that need an offer."""
    record = store.load_offer(offer_id)
    if record is None:
        return None, redirect("/", message="This offer no longer exists.", level="error")
    return record, None


def _profile() -> Profile | None:
    try:
        return profile_store.load_profile()
    except profile_store.ProfileError:
        return None


def _offer_context(record: OfferRecord) -> dict:
    """Everything the offer page shows: the offer, its verdict, its documents, its status.

    The documents need a profile to be read back and a folder to exist, so the
    page is built to work in the three states a preparation goes through: never
    prepared, prepared, and preparing right now.
    """
    offer_id = record.id
    application = tracker_store.load_application(offer_id)
    folder = application.folder if application else ""
    profile = _profile()
    prepared = bool(folder) and profile is not None and writer_store.folder_exists(folder)

    context = {
        "record": record,
        "offer": record.offer,
        "missing": service.missing_fields(record.offer),
        "ranking": ranking_store.load_ranking(offer_id),
        "application": application,
        "status": tracker_service.normalize_status(application.status if application else ""),
        "statuses": tracker_service.STATUS_LABELS,
        "followup_days": load_settings().followup_days,
        "prepared": prepared,
        "folder": folder,
        # The preparation job panel polls this page. It is shown whatever the
        # state, because starting a preparation is what creates the folder.
        "job": writer_jobs.current(),
        "status_labels": writer_jobs.STATUS_LABELS,
        "poll_url": f"/offers/{offer_id}/progress",
        "next_url": f"/offers/{offer_id}",
    }
    if prepared:
        assert profile is not None  # ``prepared`` already required it
        context.update(
            writer_view.context(
                profile,
                record,
                folder,
                poll_url=f"/offers/{offer_id}/progress",
                next_url=f"/offers/{offer_id}",
            )
        )
    return context


@router.get("")
async def offers_page():
    """The list is the board now; keep the old link working."""
    return redirect("/")


@router.get("/new", response_class=HTMLResponse)
async def offer_new(request: Request):
    return render(
        request,
        "offers/new.html",
        active="offers",
        fragments=[""],
        settings=load_settings(),
    )


@router.post("/new", response_class=HTMLResponse)
async def offer_analyze(request: Request):
    form = await request.form()
    fragments = [str(form.get(f"fragment.{index}") or "") for index in range(_number(form, "fragment_count"))]

    try:
        outcome = await service.analyze(load_settings(), fragments)
    except (clean.CleanError, LLMError) as exc:
        # Re-render instead of redirecting: a pasted fragment can be long, and
        # losing it to a one-line error message would be infuriating.
        return render(
            request,
            "offers/new.html",
            active="offers",
            fragments=_slots(fragments),
            settings=load_settings(),
            error=str(exc),
            status_code=400,
        )

    return redirect(
        f"/offers/{outcome.record.id}", message=outcome.notice, level=outcome.level
    )


@router.get("/{offer_id}", response_class=HTMLResponse)
async def offer_detail(request: Request, offer_id: int):
    record, response = _load(offer_id)
    if response is not None:
        return response
    assert record is not None
    return render(request, "offers/detail.html", active="offers", **_offer_context(record))


@router.get("/{offer_id}/progress", response_class=HTMLResponse)
async def offer_progress(request: Request, offer_id: int):
    """The job panel, plus the refreshed documents via an out-of-band swap."""
    record, response = _load(offer_id)
    if response is not None:
        return response
    assert record is not None
    return render(request, "offers/partials/progress.html", **_offer_context(record))


@router.get("/{offer_id}/source", response_class=HTMLResponse)
async def offer_source(request: Request, offer_id: int):
    record, response = _load(offer_id)
    if response is not None:
        return response
    assert record is not None
    raw, cleaned = store.load_source(offer_id) or ("", "")
    return render(
        request,
        "offers/source.html",
        active="offers",
        record=record,
        raw=raw,
        cleaned=cleaned,
    )


@router.get("/{offer_id}/edit", response_class=HTMLResponse)
async def offer_edit(request: Request, offer_id: int):
    record, response = _load(offer_id)
    if response is not None:
        return response
    assert record is not None
    return render(
        request,
        "offers/edit.html",
        active="offers",
        record_id=record.id,
        offer=record.offer,
        missing=service.missing_fields(record.offer),
        **editor.editor_view(record.offer),
    )


@router.post("/{offer_id}/edit")
async def offer_save(request: Request, offer_id: int):
    record, response = _load(offer_id)
    if response is not None:
        return response
    assert record is not None
    form = await request.form()
    store.update_offer(offer_id, editor.offer_from_form(form, record.offer))
    return redirect(f"/offers/{offer_id}", message="Offer saved.")


@router.post("/{offer_id}/reanalyze")
async def offer_reanalyze(offer_id: int):
    record, response = _load(offer_id)
    if response is not None:
        return response
    assert record is not None
    try:
        outcome = await service.reanalyze(load_settings(), record)
    except LLMError as exc:
        return redirect(f"/offers/{offer_id}", message=str(exc), level="error")
    return redirect(f"/offers/{offer_id}", message=outcome.notice, level=outcome.level)


@router.post("/{offer_id}/delete")
async def offer_delete(offer_id: int):
    record, response = _load(offer_id)
    if response is not None:
        return response
    assert record is not None
    store.delete_offer(offer_id)
    return redirect("/", message="Offer deleted.")

"""Offer analyzer routes: paste a fragment, read the analysis, complete it.

Routes stay thin (parse the request, call the service, redirect); the cleaning,
the extraction and the storage live in the sibling modules.
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.config import load_settings
from app.llm import LLMError
from app.offers import clean, editor, service, store
from app.web.templating import redirect, render

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
        return None, redirect("/offers", message="This offer no longer exists.", level="error")
    return record, None


@router.get("", response_class=HTMLResponse)
async def offers_list(request: Request):
    return render(request, "offers/list.html", active="offers", offers=store.list_offers())


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
    return render(
        request,
        "offers/detail.html",
        active="offers",
        record=record,
        offer=record.offer,
        missing=service.missing_fields(record.offer),
    )


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
    store.delete_offer(offer_id)
    return redirect("/offers", message="Offer deleted.")

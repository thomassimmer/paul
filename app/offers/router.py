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

from app import background
from app.config import load_settings
from app.llm import LLMError
from app.models import Offer, OfferRecord, Profile
from app.offers import clean, editor, service, store
from app.profiler import store as profile_store
from app.ranking import store as ranking_store
from app.tracker import service as tracker_service
from app.tracker import store as tracker_store
from app.web.templating import htmx_redirect, redirect, render
from app.writer import jobs as writer_jobs
from app.writer import store as writer_store
from app.writer import view as writer_view

router = APIRouter(prefix="/offers", tags=["offers"])

# The single-call background runs this router starts, and where their page polls.
ANALYZE = "offer_analyze"
REANALYZE = "offer_reanalyze"


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


def _nav_sections(offer: Offer, prepared: bool) -> list[dict]:
    """The offer page's sections, in reading order.

    Two groups rather than a flat list of a dozen entries: what the offer says,
    and what we made of it. Ranking sits between them, at the top level, because
    it judges the offer rather than belonging to either.
    """
    offer_cards = [
        {"anchor": "overview", "label": "Overview"},
        {"anchor": "responsibilities", "label": "Responsibilities"},
        {"anchor": "requirements", "label": "Requirements"},
        {"anchor": "keywords", "label": "Keywords"},
    ]
    # Only the cards the page actually renders: the other two are conditional.
    if offer.constraints.stated:
        offer_cards.append({"anchor": "constraints", "label": "Constraints"})
    if offer.company_info.stated:
        offer_cards.append({"anchor": "company", "label": "Company"})
    offer_cards.append({"anchor": "application-form", "label": "Application form"})

    # What the application is: the documents written for this offer, and where
    # it stands. Before a preparation, only the second part exists.
    application: list[dict] = []
    if prepared:
        application += [
            {"anchor": "checks", "label": "Checks"},
            {"anchor": "cv", "label": "CV"},
            {"anchor": "letter", "label": "Cover letter"},
            {"anchor": "answers", "label": "Form answers"},
        ]
    application.append({"anchor": "tracking", "label": "Tracking"})

    return [
        {"label": "Offer", "children": offer_cards},
        {"anchor": "ranking", "label": "Ranking"},
        {"label": "Application", "children": application},
    ]


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

    observe = background.live(REANALYZE)
    if observe is not None and observe.context.get("offer_id") != offer_id:
        observe = None

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
        # The re-analysis run, while one is in flight for this offer.
        "analysis_run": observe,
        "analysis_poll_url": f"/offers/{offer_id}/analysis-status",
        # The preparation job panel polls this page. It is shown whatever the
        # state, because starting a preparation is what creates the folder.
        "job": writer_jobs.current(),
        "status_labels": writer_jobs.STATUS_LABELS,
        "poll_url": f"/offers/{offer_id}/progress",
        "next_url": f"/offers/{offer_id}",
        # The quick navigation. Built here rather than in the template so the page
        # and its polling endpoint cannot drift: preparing an offer adds the four
        # document sections, and the poll has to render the longer list.
        "nav_label": "Sections of this offer",
        "nav_sections": _nav_sections(record.offer, prepared),
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
    settings = load_settings()

    # The local half runs here, so a fragment that cannot be read is still
    # reported on the page: there is no point starting a run for it.
    try:
        parts, cleaned = service.prepare_fragments(settings, fragments)
    except (clean.CleanError, LLMError) as exc:
        # Re-render instead of redirecting: a pasted fragment can be long, and
        # losing it to a one-line error message would be infuriating.
        return render(
            request,
            "offers/new.html",
            active="offers",
            fragments=_slots(fragments),
            settings=settings,
            error=str(exc),
            status_code=400,
        )

    await background.start(
        ANALYZE,
        "Analyzing the offer…",
        _analyze_work(settings, parts, cleaned),
    )
    return render(
        request,
        "offers/new.html",
        active="offers",
        fragments=_slots(fragments),
        settings=settings,
        run=background.current(ANALYZE),
        poll_url="/offers/new/status",
    )


def _analyze_work(settings, parts, cleaned):
    async def work(run: background.Run) -> None:
        outcome = await service.extract_and_store(settings, parts, cleaned)
        run.message = outcome.notice
        run.level = outcome.level
        run.return_url = f"/offers/{outcome.record.id}"

    return work


@router.get("/new/status", response_class=HTMLResponse)
async def offer_analyze_status(request: Request):
    """Polled by the analyze page: the run card, or a move to the new offer."""
    run = background.current(ANALYZE)
    if run is not None and not run.running and not run.error:
        background.clear(ANALYZE)
        return htmx_redirect(run.return_url, message=run.message, level=run.level)
    return render(request, "partials/background.html", run=run, poll_url="/offers/new/status")


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
    settings = load_settings()
    # The local half: a missing model or a missing source is answered at once.
    try:
        cleaned_text = service.prepare_reanalysis(settings, record)
    except LLMError as exc:
        return redirect(f"/offers/{offer_id}", message=str(exc), level="error")

    await background.start(
        REANALYZE,
        "Analyzing the offer again…",
        _reanalyze_work(settings, record, cleaned_text),
        context={"offer_id": offer_id},
    )
    return redirect(
        f"/offers/{offer_id}",
        message="Analyzing again in the background. The page updates itself.",
    )


def _reanalyze_work(settings, record: OfferRecord, cleaned_text: str):
    async def work(run: background.Run) -> None:
        outcome = await service.update_from_model(settings, record, cleaned_text)
        run.message = outcome.notice
        run.level = outcome.level
        run.return_url = f"/offers/{record.id}"

    return work


@router.get("/{offer_id}/analysis-status", response_class=HTMLResponse)
async def offer_analysis_status(request: Request, offer_id: int):
    """Polled by the offer page while a re-analysis of it is running."""
    run = background.current(REANALYZE)
    if run is None or run.context.get("offer_id") != offer_id:
        return htmx_redirect(f"/offers/{offer_id}")
    if not run.running and not run.error:
        background.clear(REANALYZE)
        return htmx_redirect(run.return_url, message=run.message, level=run.level)
    return render(
        request,
        "partials/background.html",
        run=run,
        poll_url=f"/offers/{offer_id}/analysis-status",
    )


@router.post("/{offer_id}/delete")
async def offer_delete(offer_id: int):
    record, response = _load(offer_id)
    if response is not None:
        return response
    assert record is not None
    store.delete_offer(offer_id)
    return redirect("/", message="Offer deleted.")

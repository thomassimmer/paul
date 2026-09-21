"""Application documents: prepare, save, regenerate, download, preview.

The *pages* live elsewhere: the documents are shown inside the offer page
(``app/offers/router.py``), which is where a prepared application belongs. What
stays here are the actions and the files, which are the writer's own business.

Preparing and regenerating are background tasks (see ``jobs.py``) because they are
several model calls plus a page measurement: the offer page shows their progress.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response

from app.config import load_settings
from app.models import FormQuestion, OfferRecord, Profile
from app.offers import clean as offers_clean
from app.offers import store as offers_store
from app.profiler import store as profile_store
from app.templates_engine import pdf
from app.tracker import store as tracker_store
from app.web.templating import htmx_redirect, local_url, redirect, render
from app.writer import jobs, service, store
from app.writer import view as writer_view

router = APIRouter(prefix="/applications", tags=["applications"])

DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MEDIA_TYPES = {
    store.CV_DOCX: DOCX_MEDIA,
    store.LETTER_DOCX: DOCX_MEDIA,
    store.CV_MD: "text/markdown; charset=utf-8",
    store.LETTER_MD: "text/markdown; charset=utf-8",
    store.ANSWERS_MD: "text/markdown; charset=utf-8",
    store.ATS_JSON: "application/json",
    store.NOTES_MD: "text/markdown; charset=utf-8",
}
PDF_SOURCES = {"cv.pdf": store.CV_DOCX, "letter.pdf": store.LETTER_DOCX}
# What the framed preview on the offer page may render, by short name.
PREVIEW_KINDS = {"cv": store.CV_DOCX, "letter": store.LETTER_DOCX}

BUSY_MESSAGE = "A preparation is already running. Wait for it, or stop it first."

# Sent when the modal asked for documents that are already written and a form that
# did not change: preparing again would only spend model calls on the same result.
NOTHING_TO_PREPARE = (
    "Nothing new to prepare: what you asked for is already there. Regenerate a "
    "section below, or paste the form's new questions."
)

# Sent when the modal asked for nothing at all: no document to write, no form to
# answer. Distinct from the above, because there is nothing on disk to point at.
NOTHING_ASKED = "Choose a document to write, or paste the form's questions."

# How a section is named inside a sentence, unlike ``service.SECTION_LABELS`` which
# is a title.
SECTION_PHRASES = {"cv": "CV", "letter": "cover letter", "answers": "form answers"}

# Where an action lands when it is not given a ``next``: the board, from which a
# preparation is usually started.
BOARD = "/"


def _profile() -> tuple[Profile | None, str | None]:
    try:
        return profile_store.load_profile(), None
    except profile_store.ProfileError as exc:
        return None, str(exc)


def _busy() -> bool:
    job = jobs.current()
    return job is not None and job.running


def _is_htmx(request: Request) -> bool:
    """Whether this request comes from an HTMX swap rather than a plain form post."""
    return request.headers.get("HX-Request") == "true"


def _move(request: Request, url: str, *, message: str = "", level: str = "ok") -> Response:
    """Send the reader to ``url``, whether the request came from HTMX or not.

    A plain form post redirects; an HTMX one is told to navigate the whole page,
    because a 3xx would be followed silently by the XHR instead of the browser.
    """
    if _is_htmx(request):
        return htmx_redirect(url, message=message, level=level)
    return redirect(url, message=message, level=level)


def _job_started(request: Request, record: OfferRecord) -> Response:
    """The placeholder swapped into ``#job`` when an HTMX request starts a job.

    It pulls the real panel at once, and that panel then polls itself. Answering
    this way rather than redirecting is what lets the reader keep their place.
    """
    return render(
        request,
        "writer/partials/job_pending.html",
        panel_id="job",
        poll_url=f"/offers/{record.id}/progress",
    )


# The ids the panel wrapper may have, so a posted ``panel_id`` cannot inject markup.
PANEL_IDS = {"job", "job-writing"}


def _panel_id(form) -> str:
    """The wrapper a stop or dismiss form targets, kept to the ids we know."""
    panel_id = str(form.get("panel_id") or "")
    return panel_id if panel_id in PANEL_IDS else "job"


def _refresh_job(request: Request, form, back: str, *, message: str, level: str) -> Response:
    """Re-point the job panel at its own poll endpoint, after a stop or a dismiss.

    The panel's URL is the caller's — the board's carries the table's sort and
    filter — so the form hands it back and the panel refreshes through the normal
    path. Without it, the browser is moved instead.
    """
    poll_url = local_url(form.get("poll_url"), "")
    if not poll_url:
        return _move(request, back, message=message, level=level)
    return render(
        request,
        "writer/partials/job_pending.html",
        panel_id=_panel_id(form),
        poll_url=poll_url,
    )


def _save_landing(
    request: Request,
    profile: Profile | None,
    record: OfferRecord,
    folder: str,
    *,
    message: str,
    level: str = "ok",
):
    """Where a save sends the reader.

    Without JavaScript it is the offer page, from the top. With HTMX the documents
    are re-rendered where they already are, so saving does not move the page at all:
    the reader stays on the CV or the letter they were editing.
    """
    if not _is_htmx(request) or profile is None:
        return _move(request, _offer_url(record.id), message=message, level=level)
    return render(
        request,
        "writer/partials/saved.html",
        prepared=True,
        notice={"message": message, "level": level},
        **writer_view.context(
            profile,
            record,
            folder,
            poll_url=f"/offers/{record.id}/progress",
            next_url=_offer_url(record.id),
        ),
    )


def _back(request_form, default: str) -> str:
    return local_url(request_form.get("next"), default)


def _offer_url(offer_id: int) -> str:
    return f"/offers/{offer_id}"


def _prepared(offer_id: int):
    """``(record, folder, error_response)`` for the routes that need both."""
    record = offers_store.load_offer(offer_id)
    if record is None:
        return None, "", redirect(BOARD, message="This offer no longer exists.", level="error")
    application = tracker_store.load_application(offer_id)
    folder = application.folder if application else ""
    if not folder or not store.folder_exists(folder):
        return record, "", redirect(
            _offer_url(offer_id),
            message="This application is not prepared yet.",
            level="warning",
        )
    return record, folder, None


# --- Preparing -----------------------------------------------------------------


@router.post("/{offer_id}/prepare")
async def application_prepare(offer_id: int, request: Request):
    """Prepare an application, from the modal that says what applying requires.

    The modal decides which documents are needed and pastes the form to answer;
    the work itself is a background job because answering and writing take several
    model calls. A document that is already on disk is never rewritten here — the
    Regenerate buttons are the deliberate way to redraft one.
    """
    form = await request.form()
    back = _back(form, BOARD)
    record = offers_store.load_offer(offer_id)
    if record is None:
        return _move(request, BOARD, message="This offer no longer exists.", level="error")

    profile, error = _profile()
    if profile is None:
        return _move(
            request,
            back,
            message=error or "Import your CV first: the documents are written from your profile.",
            level="warning",
        )
    settings = load_settings()
    if not settings.model.strip():
        return _move(
            request,
            back,
            message="No model configured. Set one in Settings to prepare an application.",
            level="error",
        )
    if _busy():
        return _move(request, back, message=BUSY_MESSAGE, level="warning")

    application = tracker_store.load_application(offer_id)
    if form.get("plan") is not None:
        # The preparation modal. Its box shows the questions read from the offer
        # when nothing was pasted: submitting that untouched text must not re-parse
        # it as plain lines and lose the markup's names, options and maxlength.
        want_cv = bool(form.get("cv"))
        want_letter = bool(form.get("letter"))
        posted = str(form.get("form") or "").strip()
        if posted == service.form_text(record, application).strip():
            form_source, form_changed = (
                application.form_source if application else "",
                False,
            )
        else:
            form_source, form_changed = posted, True
            questions = offers_clean.parse_questions(posted)
            if posted and not questions:
                return _move(
                    request,
                    back,
                    message=(
                        "No question could be read from the pasted form. Paste its "
                        "markup, or one question per line."
                    ),
                    level="warning",
                )
            if questions:
                record = _store_form(offer_id, record, questions)
        tracker_store.set_plan(
            offer_id, want_cv=want_cv, want_letter=want_letter, form_source=form_source
        )
    else:
        # A bare preparation, with no modal behind it: the stored plan is kept.
        want_cv = application.want_cv if application else True
        want_letter = application.want_letter if application else True
        form_changed = False

    folder = application.folder if application else ""
    sections = service.sections_to_write(
        record,
        folder,
        want_cv=want_cv,
        want_letter=want_letter,
        form_changed=form_changed,
    )
    if not sections:
        asked = want_cv or want_letter or bool(record.offer.form)
        return _move(
            request,
            back,
            message=NOTHING_TO_PREPARE if asked else NOTHING_ASKED,
            level="warning",
        )

    await jobs.start_job(settings, profile, record, kind="prepare", sections=sections)
    if _is_htmx(request):
        return _job_started(request, record)
    return _move(
        request,
        back,
        message="Preparing in the background. The page updates itself every second.",
    )


def _store_form(offer_id: int, record: OfferRecord, questions: list[FormQuestion]) -> OfferRecord:
    """Replace the offer's form with the questions just pasted, and return it.

    The form is part of the offer as read from the markup, so pasting it later is
    the same operation as pasting it with the offer: the page's "Application form"
    card, the writer and the Regenerate buttons all read the one list.
    """
    offer = record.offer.model_copy(update={"form": questions})
    offers_store.update_offer(offer_id, offer)
    return record.model_copy(update={"offer": offer})


# --- Saving and regenerating ---------------------------------------------------


@router.post("/{offer_id}/save")
async def application_save(offer_id: int, request: Request):
    record, folder, response = _prepared(offer_id)
    if response is not None:
        # HTMX would follow a 3xx silently and swap the whole offer page into the
        # documents region; send the browser there instead.
        if _is_htmx(request):
            return htmx_redirect(str(response.headers.get("location") or BOARD))
        return response
    assert record is not None
    profile, error = _profile()
    if profile is None:
        return _save_landing(
            request, profile, record, folder, message=error or "No profile.", level="warning"
        )
    if _busy():
        return _save_landing(request, profile, record, folder, message=BUSY_MESSAGE, level="warning")

    form = await request.form()

    def posted(name: str) -> str | None:
        value = form.get(name)
        return None if value is None else str(value)

    try:
        saved = service.save(
            load_settings(),
            profile,
            record,
            folder=folder,
            cv_source=posted("cv"),
            letter_source=posted("letter"),
            answers_source=posted("answers"),
        )
    except (service.WriterError, store.FolderError) as exc:
        return _save_landing(request, profile, record, folder, message=str(exc), level="error")
    return _save_landing(
        request,
        profile,
        record,
        folder,
        message=f"Saved: {', '.join(saved.sections)}. The documents were re-rendered.",
    )


@router.post("/{offer_id}/regenerate")
async def application_regenerate(offer_id: int, request: Request):
    record, folder, response = _prepared(offer_id)
    if response is not None:
        # HTMX would follow a 3xx silently and swap the whole offer page into the
        # target; send the browser there instead.
        if _is_htmx(request):
            return htmx_redirect(str(response.headers.get("location") or BOARD))
        return response
    assert record is not None
    profile, error = _profile()
    if profile is None:
        return _move(request, _offer_url(offer_id), message=error or "No profile.", level="warning")
    settings = load_settings()
    if not settings.model.strip():
        return _move(
            request,
            _offer_url(offer_id),
            message="No model configured. Set one in Settings to regenerate.",
            level="error",
        )

    form = await request.form()
    section = str(form.get("section") or "")
    if section not in service.SECTIONS:
        return _move(
            request, _offer_url(offer_id), message=f"Unknown section: {section}.", level="error"
        )
    if _busy():
        return _move(request, _offer_url(offer_id), message=BUSY_MESSAGE, level="warning")

    await jobs.start_job(
        settings,
        profile,
        record,
        kind="regenerate",
        section=section,
        instruction=str(form.get("instruction") or "").strip(),
        # An unchecked checkbox posts nothing: off by default, the section is rewritten.
        from_current=bool(form.get("from_current")),
        folder=folder,
    )
    if _is_htmx(request):
        return _job_started(request, record)
    phrase = SECTION_PHRASES.get(section, section)
    return _move(
        request,
        _offer_url(offer_id),
        message=f"Regenerating the {phrase} in the background. This page updates itself.",
    )


@router.post("/cancel")
async def applications_cancel(request: Request):
    form = await request.form()
    back = _back(form, BOARD)
    if jobs.cancel():
        message, level = "Stopping: the folder is only written at the end.", "ok"
    else:
        message, level = "Nothing is running.", "warning"
    if _is_htmx(request):
        return _refresh_job(request, form, back, message=message, level=level)
    return redirect(back, message=message, level=level)


@router.post("/dismiss")
async def applications_dismiss(request: Request):
    form = await request.form()
    expected = str(form.get("job_id") or "")
    current = jobs.current()
    if expected and current is not None and current.id != expected:
        # The finished job has already been replaced by a newer one — the offer
        # page's toast closes itself a second after it is done, and the click may
        # have been followed by another run. A 204 leaves the DOM alone.
        return Response(status_code=204)
    jobs.reset()
    if _is_htmx(request):
        # The panel simply takes itself off the page; there is nothing to fetch back.
        return render(request, "writer/partials/job_none.html", panel_id=_panel_id(form))
    return redirect(_back(form, BOARD))


# --- The old list page ---------------------------------------------------------


@router.get("")
async def applications_page():
    """Applications are shown per offer now; keep the old link working."""
    return redirect("/")


# --- Downloading ---------------------------------------------------------------


@router.get("/{offer_id}")
async def application_review(offer_id: int):
    """The review screen is the offer page now; keep the old link working."""
    return redirect(_offer_url(offer_id))


@router.get("/{offer_id}/download/{filename}")
async def application_download(offer_id: int, filename: str):
    record, folder, response = _prepared(offer_id)
    if response is not None:
        return response
    assert record is not None

    if filename in PDF_SOURCES:
        return _download_pdf(offer_id, folder, filename, PDF_SOURCES[filename])
    if filename not in store.DOWNLOADABLE:
        return redirect(
            _offer_url(offer_id), message=f"No such document: {filename}.", level="error"
        )

    try:
        path = store.file_path(folder, filename)
    except store.FolderError as exc:
        return redirect(_offer_url(offer_id), message=str(exc), level="error")
    if not path.is_file():
        return redirect(
            _offer_url(offer_id), message=f"{filename} does not exist yet.", level="error"
        )
    return FileResponse(path, media_type=MEDIA_TYPES.get(filename), filename=filename)


def _download_pdf(offer_id: int, folder: str, filename: str, source: str) -> Response:
    data = store.read_bytes(folder, source)
    if data is None:
        return redirect(
            _offer_url(offer_id),
            message=f"{source} does not exist yet.",
            level="error",
        )
    converted = pdf.to_pdf(data)
    if converted is None:
        return redirect(
            _offer_url(offer_id),
            message="PDF export needs LibreOffice, which is not installed here.",
            level="warning",
        )
    return Response(
        converted,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- The framed preview --------------------------------------------------------


@router.get("/{offer_id}/preview/{kind}")
async def application_preview(offer_id: int, kind: str):
    """The saved document as a PDF, for the frame on the offer page.

    Converted on demand from the DOCX that was last written, so the frame always
    matches what a download would give. The URL carries a revision token, which
    lets the browser keep the PDF until the document is saved again. When the
    frame cannot be filled, a plain-text body is returned on purpose: a redirect
    would embed the whole offer page inside it.
    """
    record, folder, response = _prepared(offer_id)
    if response is not None:
        return response
    assert record is not None

    source = PREVIEW_KINDS.get(kind)
    if source is None:
        return Response("Unknown document.", status_code=404, media_type="text/plain")
    data = store.read_bytes(folder, source)
    if data is None:
        return Response(
            f"{source} does not exist yet.", status_code=404, media_type="text/plain"
        )

    # Off the event loop: a conversion takes a second or two and the offer page
    # keeps polling while it runs.
    converted = await asyncio.to_thread(pdf.to_pdf, data)
    if converted is None:
        return Response(
            "The PDF preview needs LibreOffice, which is not installed here.",
            status_code=503,
            media_type="text/plain",
        )
    return Response(
        converted,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{kind}.pdf"',
            # The revision in the URL changes with the document, so keeping the
            # copy is safe: a save asks for a different URL.
            "Cache-Control": "private, max-age=31536000",
        },
    )

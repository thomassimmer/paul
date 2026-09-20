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
from app.models import Profile
from app.offers import store as offers_store
from app.profiler import store as profile_store
from app.templates_engine import pdf
from app.tracker import store as tracker_store
from app.web.templating import local_url, redirect
from app.writer import jobs, service, store

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
    form = await request.form()
    back = _back(form, BOARD)
    record = offers_store.load_offer(offer_id)
    if record is None:
        return redirect(BOARD, message="This offer no longer exists.", level="error")

    profile, error = _profile()
    if profile is None:
        return redirect(
            back,
            message=error or "Import your CV first: the documents are written from your profile.",
            level="warning",
        )
    settings = load_settings()
    if not settings.model.strip():
        return redirect(
            back,
            message="No model configured. Set one in Settings to prepare an application.",
            level="error",
        )
    if _busy():
        return redirect(back, message=BUSY_MESSAGE, level="warning")

    await jobs.start_job(settings, profile, record, kind="prepare")
    return redirect(
        back,
        message="Preparing in the background. The page updates itself every 2 seconds.",
    )


# --- Saving and regenerating ---------------------------------------------------


@router.post("/{offer_id}/save")
async def application_save(offer_id: int, request: Request):
    record, folder, response = _prepared(offer_id)
    if response is not None:
        return response
    assert record is not None
    profile, error = _profile()
    if profile is None:
        return redirect(_offer_url(offer_id), message=error or "No profile.", level="warning")
    if _busy():
        return redirect(_offer_url(offer_id), message=BUSY_MESSAGE, level="warning")

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
        return redirect(_offer_url(offer_id), message=str(exc), level="error")
    return redirect(
        _offer_url(offer_id),
        message=f"Saved: {', '.join(saved.sections)}. The documents were re-rendered.",
    )


@router.post("/{offer_id}/regenerate")
async def application_regenerate(offer_id: int, request: Request):
    record, folder, response = _prepared(offer_id)
    if response is not None:
        return response
    assert record is not None
    profile, error = _profile()
    if profile is None:
        return redirect(_offer_url(offer_id), message=error or "No profile.", level="warning")
    settings = load_settings()
    if not settings.model.strip():
        return redirect(
            _offer_url(offer_id),
            message="No model configured. Set one in Settings to regenerate.",
            level="error",
        )

    form = await request.form()
    section = str(form.get("section") or "")
    if section not in service.SECTIONS:
        return redirect(
            _offer_url(offer_id), message=f"Unknown section: {section}.", level="error"
        )
    if _busy():
        return redirect(_offer_url(offer_id), message=BUSY_MESSAGE, level="warning")

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
    phrase = SECTION_PHRASES.get(section, section)
    return redirect(
        _offer_url(offer_id),
        message=f"Regenerating the {phrase} in the background. This page updates itself.",
    )


@router.post("/cancel")
async def applications_cancel(request: Request):
    form = await request.form()
    back = _back(form, BOARD)
    if jobs.cancel():
        return redirect(back, message="Stopping: the folder is only written at the end.")
    return redirect(back, message="Nothing is running.", level="warning")


@router.post("/dismiss")
async def applications_dismiss(request: Request):
    form = await request.form()
    jobs.reset()
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

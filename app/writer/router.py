"""Applications routes: pick an offer, prepare its documents, review, download.

Routes stay thin: they read the request, start a background job or call the
service, and redirect. Preparing and regenerating are background tasks (see
``jobs.py``) because they are several model calls plus a page measurement: the page
shows their progress and polls it every two seconds, exactly like the ranker.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from app.config import load_settings
from app.models import Profile
from app.offers import store as offers_store
from app.profiler import store as profile_store
from app.ranking import store as ranking_store
from app.templates_engine import pdf
from app.tracker import store as tracker_store
from app.web.templating import redirect, render
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

BUSY_MESSAGE = "A preparation is already running. Wait for it, or stop it first."

# How a section is named inside a sentence, unlike ``service.SECTION_LABELS`` which
# is a title.
SECTION_PHRASES = {"cv": "CV", "letter": "cover letter", "answers": "form answers"}


def _profile() -> tuple[Profile | None, str | None]:
    try:
        return profile_store.load_profile(), None
    except profile_store.ProfileError as exc:
        return None, str(exc)


def _next(form, default: str) -> str:
    """Where a cancel or a dismiss should land. Only inside this section."""
    value = str(form.get("next") or "")
    return value if value.startswith("/applications") else default


def _busy() -> bool:
    job = jobs.current()
    return job is not None and job.running


def _index_context() -> dict:
    settings = load_settings()
    profile, profile_error = _profile()
    return {
        "settings": settings,
        "has_profile": profile is not None,
        "profile_error": profile_error,
        "has_model": bool(settings.model.strip()),
        "rows": service.application_rows(
            offers_store.list_offers(),
            ranking_store.list_rankings(),
            tracker_store.list_applications(),
            settings,
            profile if profile is not None else Profile(),
        ),
        "job": jobs.current(),
        "status_labels": jobs.STATUS_LABELS,
        "poll_url": "/applications/progress",
        "next_url": "/applications",
    }


def _review_context(profile: Profile, record, folder: str) -> dict:
    return {
        "record": record,
        "offer": record.offer,
        "folder": folder,
        "target": load_settings().target_pages,
        "review": service.load_review(profile, record, folder),
        "job": jobs.current(),
        "status_labels": jobs.STATUS_LABELS,
        "poll_url": f"/applications/{record.id}/progress",
        "next_url": f"/applications/{record.id}",
    }


# --- The list page -------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
async def applications_page(request: Request):
    return render(request, "writer/index.html", active="applications", **_index_context())


@router.get("/progress", response_class=HTMLResponse)
async def applications_progress(request: Request):
    """The job panel, plus the refreshed list via an out-of-band swap."""
    return render(request, "writer/partials/list_progress.html", **_index_context())


@router.post("/cancel")
async def applications_cancel(request: Request):
    form = await request.form()
    back = _next(form, "/applications")
    if jobs.cancel():
        return redirect(back, message="Stopping: the folder is only written at the end.")
    return redirect(back, message="Nothing is running.", level="warning")


@router.post("/dismiss")
async def applications_dismiss(request: Request):
    form = await request.form()
    jobs.reset()
    return redirect(_next(form, "/applications"))


# --- Preparing -----------------------------------------------------------------


@router.post("/{offer_id}/prepare")
async def application_prepare(offer_id: int):
    record = offers_store.load_offer(offer_id)
    if record is None:
        return redirect("/applications", message="This offer no longer exists.", level="error")

    profile, error = _profile()
    if profile is None:
        return redirect(
            "/applications",
            message=error or "Import your CV first: the documents are written from your profile.",
            level="warning",
        )
    settings = load_settings()
    if not settings.model.strip():
        return redirect(
            "/applications",
            message="No model configured. Set one in Settings to prepare an application.",
            level="error",
        )
    if _busy():
        return redirect("/applications", message=BUSY_MESSAGE, level="warning")

    await jobs.start_job(settings, profile, record, kind="prepare")
    return redirect(
        "/applications",
        message="Preparing in the background. This page updates itself every 2 seconds.",
    )


def _prepared(offer_id: int):
    """``(record, folder, error_response)`` for the routes that need both."""
    record = offers_store.load_offer(offer_id)
    if record is None:
        return None, "", redirect(
            "/applications", message="This offer no longer exists.", level="error"
        )
    application = tracker_store.load_application(offer_id)
    folder = application.folder if application else ""
    if not folder or not store.folder_exists(folder):
        return record, "", redirect(
            "/applications",
            message="This application is not prepared yet.",
            level="warning",
        )
    return record, folder, None


# --- Reviewing -----------------------------------------------------------------


@router.get("/{offer_id}/progress", response_class=HTMLResponse)
async def application_progress(request: Request, offer_id: int):
    """The job panel, plus the refreshed review body via an out-of-band swap."""
    record, folder, response = _prepared(offer_id)
    if response is not None:
        return response
    assert record is not None
    profile, error = _profile()
    if profile is None:
        return redirect("/applications", message=error or "No profile.", level="warning")
    return render(
        request,
        "writer/partials/review_progress.html",
        **_review_context(profile, record, folder),
    )


@router.get("/{offer_id}", response_class=HTMLResponse)
async def application_review(request: Request, offer_id: int):
    record, folder, response = _prepared(offer_id)
    if response is not None:
        return response
    assert record is not None
    profile, error = _profile()
    if profile is None:
        return redirect(
            "/applications",
            message=error or "Import your CV first: the review screen checks against your profile.",
            level="warning",
        )
    return render(
        request,
        "writer/review.html",
        active="applications",
        **_review_context(profile, record, folder),
    )


@router.post("/{offer_id}/save")
async def application_save(request: Request, offer_id: int):
    record, folder, response = _prepared(offer_id)
    if response is not None:
        return response
    assert record is not None
    profile, error = _profile()
    if profile is None:
        return redirect("/applications", message=error or "No profile.", level="warning")
    if _busy():
        return redirect(f"/applications/{offer_id}", message=BUSY_MESSAGE, level="warning")

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
        return redirect(f"/applications/{offer_id}", message=str(exc), level="error")
    return redirect(
        f"/applications/{offer_id}",
        message=f"Saved: {', '.join(saved.sections)}. The documents were re-rendered.",
    )


@router.post("/{offer_id}/regenerate")
async def application_regenerate(request: Request, offer_id: int):
    record, folder, response = _prepared(offer_id)
    if response is not None:
        return response
    assert record is not None
    profile, error = _profile()
    if profile is None:
        return redirect("/applications", message=error or "No profile.", level="warning")
    settings = load_settings()
    if not settings.model.strip():
        return redirect(
            f"/applications/{offer_id}",
            message="No model configured. Set one in Settings to regenerate.",
            level="error",
        )

    form = await request.form()
    section = str(form.get("section") or "")
    if section not in service.SECTIONS:
        return redirect(
            f"/applications/{offer_id}", message=f"Unknown section: {section}.", level="error"
        )
    if _busy():
        return redirect(f"/applications/{offer_id}", message=BUSY_MESSAGE, level="warning")

    await jobs.start_job(
        settings,
        profile,
        record,
        kind="regenerate",
        section=section,
        instruction=str(form.get("instruction") or "").strip(),
        folder=folder,
    )
    phrase = SECTION_PHRASES.get(section, section)
    return redirect(
        f"/applications/{offer_id}",
        message=f"Regenerating the {phrase} in the background. This page updates itself.",
    )


# --- Downloading ---------------------------------------------------------------


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
            f"/applications/{offer_id}", message=f"No such document: {filename}.", level="error"
        )

    try:
        path = store.file_path(folder, filename)
    except store.FolderError as exc:
        return redirect(f"/applications/{offer_id}", message=str(exc), level="error")
    if not path.is_file():
        return redirect(
            f"/applications/{offer_id}", message=f"{filename} does not exist yet.", level="error"
        )
    return FileResponse(path, media_type=MEDIA_TYPES.get(filename), filename=filename)


def _download_pdf(offer_id: int, folder: str, filename: str, source: str) -> Response:
    data = store.read_bytes(folder, source)
    if data is None:
        return redirect(
            f"/applications/{offer_id}",
            message=f"{source} does not exist yet.",
            level="error",
        )
    converted = pdf.to_pdf(data)
    if converted is None:
        return redirect(
            f"/applications/{offer_id}",
            message="PDF export needs LibreOffice, which is not installed here.",
            level="warning",
        )
    return Response(
        converted,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

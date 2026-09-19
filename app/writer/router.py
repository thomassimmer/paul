"""Applications routes: pick an offer, prepare its documents, review, download.

Routes stay thin: they read the request, call ``service``, and redirect. Preparing
is the one slow request of the app — three or four model calls, plus LibreOffice
for the page count — which is acceptable for a single offer chosen on purpose,
unlike a run over twenty of them.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from app.config import load_settings
from app.llm import LLMError
from app.models import Profile
from app.offers import store as offers_store
from app.profiler import store as profile_store
from app.ranking import store as ranking_store
from app.templates_engine import pdf
from app.templates_engine.extract import TemplateError
from app.tracker import store as tracker_store
from app.web.templating import redirect, render
from app.writer import service, store

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
MAX_FLASH = 500


def _profile() -> tuple[Profile | None, str | None]:
    try:
        return profile_store.load_profile(), None
    except profile_store.ProfileError as exc:
        return None, str(exc)


def _summarize(base: str, warnings: list[str]) -> tuple[str, str]:
    """One flash message, shortened: a warning list can be long."""
    if not warnings:
        return base, "ok"
    joined = " ".join(warnings)
    if len(joined) > MAX_FLASH:
        joined = joined[: MAX_FLASH - 1].rstrip() + "…"
    return f"{base} {joined}", "warning"


@router.get("", response_class=HTMLResponse)
async def applications_page(request: Request):
    settings = load_settings()
    profile, profile_error = _profile()
    rows = service.application_rows(
        offers_store.list_offers(),
        ranking_store.list_rankings(),
        tracker_store.list_applications(),
        settings,
        profile if profile is not None else Profile(),
    )
    return render(
        request,
        "writer/index.html",
        active="applications",
        settings=settings,
        has_profile=profile is not None,
        profile_error=profile_error,
        has_model=bool(settings.model.strip()),
        rows=rows,
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

    try:
        prepared = await service.prepare(settings, profile, record)
    except (LLMError, TemplateError) as exc:
        return redirect("/applications", message=str(exc), level="error")

    message, level = _summarize(f"Application prepared in {prepared.folder}.", prepared.warnings)
    return redirect(f"/applications/{offer_id}", message=message, level=level)


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
        record=record,
        offer=record.offer,
        folder=folder,
        target=load_settings().target_pages,
        review=service.load_review(profile, record, folder),
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

    form = await request.form()
    section = str(form.get("section") or "")
    instruction = str(form.get("instruction") or "").strip()
    settings = load_settings()
    if not settings.model.strip():
        return redirect(
            f"/applications/{offer_id}",
            message="No model configured. Set one in Settings to regenerate.",
            level="error",
        )

    warnings: list[str] = []
    try:
        await service.regenerate(
            settings,
            profile,
            record,
            folder=folder,
            section=section,
            instruction=instruction,
            warnings=warnings,
        )
    except (LLMError, service.WriterError, store.FolderError) as exc:
        return redirect(f"/applications/{offer_id}", message=str(exc), level="error")

    label = service.SECTION_LABELS.get(section, section)
    message, level = _summarize(f"{label} regenerated.", warnings)
    return redirect(f"/applications/{offer_id}", message=message, level=level)


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

"""Template actions: import a DOCX, check the roles that were detected, or reset.

These are settings, so they render on the settings page (``app/web/routes/
settings.py``); what lives here is what *changes*. The roles are shown as a form
the user can correct: detection is good, not infallible, and a wrongly read
template would silently produce wrongly styled documents.
"""

from __future__ import annotations

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse

from app.config import load_settings
from app.templates_engine import analyze, store
from app.templates_engine import view
from app.templates_engine.extract import TemplateError
from app.templates_engine.roles import ROLES, ROLE_LABELS
from app.web.templating import redirect, render

router = APIRouter(prefix="/templates", tags=["templates"])

# Landing back where the templates are shown: they are a settings section now.
SETTINGS = "/settings#templates"


@router.get("")
async def templates_page():
    """The templates are a component of the settings; keep the old link working."""
    return redirect(SETTINGS)


@router.post("/{kind}")
async def template_upload(request: Request, kind: str, file: UploadFile | None = File(None)):
    if kind not in view.KINDS:
        return redirect(SETTINGS, message="Unknown template kind.", level="error")

    if file is None or not file.filename:
        return redirect(SETTINGS, message="Choose a .docx file first.", level="error")
    data = await file.read()
    if len(data) > view.MAX_UPLOAD_BYTES:
        return redirect(SETTINGS, message="That file is larger than 10 MB.", level="error")

    try:
        blueprint = await analyze.analyze(load_settings(), data, kind)
    except TemplateError as exc:
        return redirect(SETTINGS, message=str(exc), level="error")

    store.save_custom(kind, data, blueprint)
    found = ", ".join(sorted(set(blueprint.roles())))
    message = f"{view.KINDS[kind]} template imported: {len(blueprint.blocks)} blocks, roles — {found}."
    if blueprint.notes:
        message += " " + " ".join(blueprint.notes)
    return redirect(f"/templates/{kind}", message=message)


@router.get("/{kind}", response_class=HTMLResponse)
async def template_preview(request: Request, kind: str):
    if kind not in view.KINDS:
        return redirect(SETTINGS, message="Unknown template kind.", level="error")
    if not store.has_custom(kind):
        return redirect(
            SETTINGS, message=f"No {view.KINDS[kind]} template imported yet.", level="warning"
        )

    try:
        blueprint = store.load_blueprint(kind)
    except TemplateError as exc:
        return redirect(SETTINGS, message=str(exc), level="error")

    return render(
        request,
        "templates_engine/preview.html",
        active="settings",
        kind=kind,
        label=view.KINDS[kind],
        blocks=blueprint.blocks,
        notes=blueprint.notes,
        allowed=ROLES.get(kind, ()),
        labels=ROLE_LABELS,
    )


@router.post("/{kind}/roles")
async def template_roles(request: Request, kind: str):
    if kind not in view.KINDS:
        return redirect(SETTINGS, message="Unknown template kind.", level="error")
    form = await request.form()
    try:
        count = int(str(form.get("block_count") or "0"))
    except ValueError:
        count = 0

    roles = [str(form.get(f"role.{index}") or "") for index in range(count)]
    try:
        store.save_roles(kind, roles)
    except TemplateError as exc:
        return redirect(SETTINGS, message=str(exc), level="error")
    return redirect(f"/templates/{kind}", message="Roles saved.")


@router.post("/{kind}/reset")
async def template_reset(kind: str):
    if kind not in view.KINDS:
        return redirect(SETTINGS, message="Unknown template kind.", level="error")
    store.delete_custom(kind)
    return redirect(SETTINGS, message=f"{view.KINDS[kind]} template removed: the default is used again.")

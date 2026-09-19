"""Profiler routes: import a CV, run the interview, view and edit the profile.

Routes stay thin: reading the request, calling the profiler, and redirecting
(Post/Redirect/Get with a flash message). All the logic lives in the sibling
modules.
"""

from __future__ import annotations

import yaml
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from app.config import load_settings
from app.models import Profile
from app.profiler import cv, editor, interview, service, store
from app.web.templating import redirect, render

MAX_UPLOAD_BYTES = 10 * 1024 * 1024

router = APIRouter(prefix="/profiler", tags=["profiler"])


def _load() -> tuple[Profile | None, str | None]:
    """Return ``(profile, error)``; a broken file is reported, not raised."""
    try:
        return store.load_profile(), None
    except store.ProfileError as exc:
        return None, str(exc)


def _need_profile():
    """Return ``(profile, redirect_response)`` for routes that need a profile."""
    profile, error = _load()
    if profile is None:
        return None, redirect(
            "/profiler/import",
            message=error or "Import your CV first.",
            level="error" if error else "warning",
        )
    return profile, None


@router.get("", response_class=HTMLResponse)
async def profile_view(request: Request):
    profile, response = _need_profile()
    if response is not None:
        return response
    assert profile is not None
    return render(
        request,
        "profiler/profile.html",
        active="profiler",
        profile=profile,
        summary=service.summary(profile),
    )


@router.get("/import", response_class=HTMLResponse)
async def import_page(request: Request):
    profile, _ = _load()
    return render(
        request,
        "profiler/import.html",
        active="profiler",
        has_profile=profile is not None,
        settings=load_settings(),
    )


@router.post("/import")
async def import_submit(
    request: Request,
    file: UploadFile | None = File(None),
    text: str = Form(""),
    replace: str = Form(""),
):
    existing, _ = _load()
    if existing is not None and replace != "1":
        return redirect(
            "/profiler/import",
            message="A profile already exists. Tick “replace my profile” to import anyway.",
            level="error",
        )

    filename, data = "", None
    if file is not None and file.filename:
        filename = file.filename
        data = await file.read()
        if len(data) > MAX_UPLOAD_BYTES:
            limit = MAX_UPLOAD_BYTES // (1024 * 1024)
            return redirect(
                "/profiler/import",
                message=f"The file is larger than {limit} MB.",
                level="error",
            )

    try:
        outcome = await service.import_cv(
            load_settings(), filename=filename, data=data, text=text
        )
    except cv.CvError as exc:
        return redirect("/profiler/import", message=str(exc), level="error")

    # With no experiences there is nothing to interview about: go and edit.
    target = "/profiler/interview" if outcome.profile.experiences else "/profiler/edit"
    return redirect(target, message=outcome.notice, level=outcome.level)


@router.get("/interview", response_class=HTMLResponse)
async def interview_page(request: Request):
    profile, response = _need_profile()
    if response is not None:
        return response
    assert profile is not None
    if not profile.experiences:
        return redirect(
            "/profiler/edit",
            message="Add at least one experience first: the interview is built from them.",
            level="warning",
        )
    return render(
        request,
        "profiler/interview.html",
        active="profiler",
        profile=profile,
        step=interview.next_step(profile, store.skipped_keys()),
    )


@router.post("/interview")
async def interview_answer(
    key: str = Form(...),
    answer: str = Form(""),
):
    profile, response = _need_profile()
    if response is not None:
        return response
    assert profile is not None
    updated, notice = await service.apply_interview_answer(
        load_settings(), profile, key, answer
    )
    store.save_profile(updated)
    if notice:
        return redirect("/profiler/interview", message=notice, level="warning")
    return redirect("/profiler/interview")


@router.post("/interview/skip")
async def interview_skip(key: str = Form(...)):
    store.skip_key(key)
    return redirect("/profiler/interview")


@router.post("/interview/restart")
async def interview_restart():
    store.clear_skips()
    return redirect("/profiler/interview", message="Skipped questions are back.")


@router.get("/edit", response_class=HTMLResponse)
async def edit_page(request: Request):
    profile, _ = _load()
    profile = profile or Profile()
    return render(
        request,
        "profiler/edit.html",
        active="profiler",
        profile=profile,
        **editor.editor_view(profile),
    )


@router.post("/edit")
async def edit_save(request: Request):
    form = await request.form()
    current, _ = _load()
    store.save_profile(editor.profile_from_form(form, current))
    return redirect("/profiler", message="Profile saved.")


@router.get("/yaml", response_class=HTMLResponse)
async def yaml_page(request: Request):
    text = ""
    if store.PROFILE_PATH.exists():
        text = store.PROFILE_PATH.read_text(encoding="utf-8")
    return render(request, "profiler/yaml.html", active="profiler", yaml_text=text)


@router.post("/yaml", response_class=HTMLResponse)
async def yaml_save(request: Request, yaml_text: str = Form("")):
    try:
        raw = yaml.safe_load(yaml_text) or {}
        if not isinstance(raw, dict):
            raise ValueError("the file must contain a YAML mapping")
        profile = Profile.model_validate(raw)
    except Exception as exc:
        # Re-render with the submitted text so nothing the user typed is lost.
        return render(
            request,
            "profiler/yaml.html",
            active="profiler",
            yaml_text=yaml_text,
            error=str(exc),
            status_code=400,
        )
    store.save_profile(profile)
    return redirect("/profiler", message="Profile saved from YAML.")


@router.get("/profile.yaml")
async def download_profile():
    if not store.PROFILE_PATH.exists():
        raise HTTPException(status_code=404, detail="No profile yet.")
    return FileResponse(
        store.PROFILE_PATH, media_type="application/yaml", filename="profile.yaml"
    )

"""Profiler routes: import a CV, run the interview, view and edit the profile.

Routes stay thin: reading the request, calling the profiler, and redirecting
(Post/Redirect/Get with a flash message). All the logic lives in the sibling
modules.
"""

from __future__ import annotations

import yaml
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from app import background
from app.config import load_settings
from app.llm import LLMError
from app.models import Profile
from app.profiler import cv, editor, interview, service, store
from app.web.templating import htmx_redirect, redirect, render

MAX_UPLOAD_BYTES = 10 * 1024 * 1024

router = APIRouter(prefix="/profiler", tags=["profiler"])

# The single-call background run an import starts, and where its page polls.
IMPORT = "profile_import"


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

    # Extracting the text is local and fast, and a file that cannot be read is
    # worth saying at once: only drafting the profile needs the model.
    try:
        prepared = service.read_cv(filename=filename, data=data, text=text)
    except cv.CvError as exc:
        return redirect("/profiler/import", message=str(exc), level="error")

    await background.start(
        IMPORT,
        "Drafting your profile…",
        _import_work(load_settings(), prepared),
    )
    return render(
        request,
        "profiler/import.html",
        active="profiler",
        has_profile=existing is not None,
        settings=load_settings(),
        run=background.current(IMPORT),
        poll_url="/profiler/import/status",
    )


def _import_work(settings, prepared: service.PreparedImport):
    async def work(run: background.Run) -> None:
        outcome = await service.draft_profile_into(settings, prepared)
        run.message = outcome.notice
        run.level = outcome.level
        # With no experiences there is nothing to interview about: go and edit.
        run.return_url = "/profiler/interview" if outcome.profile.experiences else "/profiler/edit"

    return work


@router.get("/import/status", response_class=HTMLResponse)
async def import_status(request: Request):
    """Polled by the import page: the run card, or a move to the profile."""
    run = background.current(IMPORT)
    if run is not None and not run.running and not run.error:
        background.clear(IMPORT)
        return htmx_redirect(run.return_url, message=run.message, level=run.level)
    return render(request, "partials/background.html", run=run, poll_url="/profiler/import/status")


NO_MODEL = (
    "No model configured: set one in Settings, and the interview can ask you "
    "questions and write your answers into the profile."
)


def _is_htmx(request: Request) -> bool:
    """Whether this request comes from an HTMX swap rather than a plain form post."""
    return request.headers.get("HX-Request") == "true"


def _interview_profile():
    """``(profile, response)`` for the interview routes, experiences included."""
    profile, response = _need_profile()
    if response is not None:
        return None, response
    assert profile is not None
    if not profile.experiences:
        return None, redirect(
            "/profiler/edit",
            message="Add at least one experience first: the interview asks about them.",
            level="warning",
        )
    return profile, None


def _interview(
    request: Request,
    profile: Profile,
    *,
    turn: interview.Turn | None = None,
    result: interview.DraftResult | None = None,
    error: str = "",
    draft: str = "",
):
    """Answer an interview action: swap the card, or render the whole page.

    The answer form posts here too, so the interview behaves the same without
    JavaScript: with it only the card moves, without it every turn is a page.
    """
    context = {"turn": turn, "result": result, "error": error, "draft": draft}
    if _is_htmx(request):
        return render(request, "profiler/partials/interview.html", **context)
    return render(request, "profiler/interview.html", active="profiler", profile=profile, **context)


async def _ask_next(request: Request):
    """Compute the next question, from the profile as it stands."""
    profile, response = _interview_profile()
    if response is not None:
        return response
    assert profile is not None
    settings = load_settings()
    if not settings.model.strip():
        return _interview(request, profile, error=NO_MODEL)
    try:
        turn = await service.next_question(settings, profile)
    except LLMError as exc:
        return _interview(request, profile, error=str(exc))
    return _interview(request, profile, turn=turn)


@router.get("/interview", response_class=HTMLResponse)
async def interview_page(request: Request):
    """The interview: a card that asks the model for its first question on load."""
    profile, response = _interview_profile()
    if response is not None:
        return response
    assert profile is not None
    return render(request, "profiler/interview.html", active="profiler", profile=profile)


@router.get("/interview/next", response_class=HTMLResponse)
async def interview_next(request: Request):
    """The next question. Computed now, never stored."""
    return await _ask_next(request)


@router.post("/interview/answer", response_class=HTMLResponse)
async def interview_answer(
    request: Request,
    question: str = Form(""),
    answer: str = Form(""),
):
    """Send one answer: the model writes it, then asks the next question."""
    profile, response = _interview_profile()
    if response is not None:
        return response
    assert profile is not None
    settings = load_settings()
    if not settings.model.strip():
        return _interview(request, profile, error=NO_MODEL, draft=answer)
    try:
        result, turn = await service.answer_question(settings, profile, question, answer)
    except LLMError as exc:
        # Nothing was written and the question was not marked as asked, so the
        # candidate's text is handed back with the error and can be retried.
        return _interview(
            request,
            profile,
            turn=interview.Turn(question=interview.DraftQuestion(prompt=question)),
            error=str(exc),
            draft=answer,
        )
    return _interview(request, profile, turn=turn, result=result)


@router.post("/interview/skip", response_class=HTMLResponse)
async def interview_skip(request: Request, question: str = Form("")):
    """Move to another question without answering this one.

    The skipped question is remembered like any other, so the model is told about
    it and does not come back to it.
    """
    profile, response = _interview_profile()
    if response is not None:
        return response
    assert profile is not None
    settings = load_settings()
    if not settings.model.strip():
        return _interview(request, profile, error=NO_MODEL)
    try:
        turn = await service.skip_question(settings, profile, question)
    except LLMError as exc:
        return _interview(request, profile, error=str(exc))
    return _interview(request, profile, turn=turn)


@router.post("/interview/forget")
async def interview_forget():
    """Start the interview over: every question may be asked again."""
    store.forget_questions()
    return redirect(
        "/profiler/interview", message="The interview starts over."
    )


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

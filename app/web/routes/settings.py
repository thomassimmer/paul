"""Settings: the LLM provider, output preferences, filter rules and wishes."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import (
    LANGUAGES,
    Settings,
    TargetPages,
    format_wishes,
    load_settings,
    parse_wishes,
    save_settings,
)
from app.llm import test_connection
from app.templates_engine import view as templates_view
from app.web.templating import render

router = APIRouter()


def _form_to_settings(form: Mapping[str, object], current: Settings) -> tuple[Settings, list[str]]:
    """Build Settings from a submitted form, collecting human-readable errors.

    Empty inputs fall back to the current value (that is how the API key field
    works: leaving it blank keeps the stored key, unless "remove" is ticked).
    """
    errors: list[str] = []

    language = str(form.get("output_language") or "auto").strip()
    if language not in LANGUAGES:
        errors.append(f"Unknown output language “{language}”; kept “auto”.")
        language = "auto"

    try:
        followup_days = int(str(form.get("followup_days") or 7).strip())
        if followup_days < 0:
            raise ValueError
    except ValueError:
        errors.append("Follow-up delay must be a number of days (0 or more); kept 7.")
        followup_days = 7

    page_targets: dict[str, int] = {}
    for key in ("cv", "letter"):
        default = getattr(current.target_pages, key)
        try:
            value = int(str(form.get(f"target_pages_{key}") or default).strip())
            if value < 1:
                raise ValueError
        except ValueError:
            errors.append(f"Target length for the {key} must be at least 1 page; kept {default}.")
            value = default
        page_targets[key] = value

    api_key = str(form.get("api_key") or "").strip()
    if form.get("clear_api_key"):
        api_key = ""
    elif not api_key:
        api_key = current.api_key

    settings = Settings(
        model=str(form.get("model") or "").strip(),
        api_key=api_key,
        api_base=str(form.get("api_base") or "").strip(),
        output_language=language,
        followup_days=followup_days,
        target_pages=TargetPages(**page_targets),
        filter_rules=str(form.get("filter_rules") or "").strip(),
        wishes=parse_wishes(str(form.get("wishes") or "")),
    )
    return settings, errors


def _render_settings(
    request: Request,
    settings: Settings,
    *,
    errors: list[str] | None = None,
    saved: bool = False,
    test: tuple[bool, str] | None = None,
) -> HTMLResponse:
    ok, message = (None, None)
    if test is not None:
        ok, message = test
    return render(
        request,
        "settings.html",
        active="settings",
        settings=settings,
        languages=LANGUAGES,
        wishes_text=format_wishes(settings.wishes),
        has_api_key=bool(settings.api_key),
        templates=templates_view.kinds(),
        errors=errors or [],
        saved=saved,
        test=test,
        ok=ok,
        message=message,
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, saved: bool = False) -> HTMLResponse:
    return _render_settings(request, load_settings(), saved=saved)


@router.post("/settings")
async def settings_save(request: Request):
    form = await request.form()
    settings, errors = _form_to_settings(form, load_settings())
    if errors:
        return _render_settings(request, settings, errors=errors)
    save_settings(settings)
    return RedirectResponse("/settings?saved=1", status_code=303)


@router.post("/settings/test", response_class=HTMLResponse)
async def settings_test(request: Request) -> HTMLResponse:
    """Test the submitted provider settings without saving them."""
    form = await request.form()
    settings, _ = _form_to_settings(form, load_settings())
    result = await test_connection(settings)
    # HTMX swaps a fragment; a plain form post re-renders the whole page.
    if request.headers.get("hx-request", "").lower() == "true":
        ok, message = result
        return render(request, "partials/test_result.html", ok=ok, message=message)
    return _render_settings(request, settings, test=result)

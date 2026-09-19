"""FastAPI app: dashboard and settings page.

Everything runs in one process with no front-end build step: Jinja templates,
a little HTMX (progressively enhanced: every action also has a plain-form
fallback), SQLite for domain data, and ``data/settings.json`` for settings.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db
from app.config import (
    DATA_DIR,
    LANGUAGES,
    Settings,
    TargetPages,
    format_wishes,
    load_settings,
    parse_wishes,
    save_settings,
)
from app.llm import test_connection

WEB_DIR = Path(__file__).parent / "web"
templates = Jinja2Templates(directory=WEB_DIR / "templates")


@asynccontextmanager
async def lifespan(_: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db()
    yield


app = FastAPI(title="Paul (Emploi)", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")


def _form_to_settings(form, current: Settings) -> tuple[Settings, list[str]]:
    """Build Settings from a submitted form, collecting human-readable errors.

    Empty inputs fall back to the current value (that is how the API key field
    works: leaving it blank keeps the stored key, unless "remove" is ticked).
    """
    errors: list[str] = []

    language = (form.get("output_language") or "auto").strip()
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
    context = {
        "active": "settings",
        "settings": settings,
        "languages": LANGUAGES,
        "wishes_text": format_wishes(settings.wishes),
        "has_api_key": bool(settings.api_key),
        "errors": errors or [],
        "saved": saved,
        "test": test,
    }
    if test is not None:
        context["ok"], context["message"] = test
    return templates.TemplateResponse(request, "settings.html", context)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    settings = load_settings()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "active": "home",
            "settings": settings,
            "configured": bool(settings.model),
        },
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, saved: bool = False) -> HTMLResponse:
    return _render_settings(request, load_settings(), saved=saved)


@app.post("/settings")
async def settings_save(request: Request):
    form = await request.form()
    settings, errors = _form_to_settings(form, load_settings())
    if errors:
        return _render_settings(request, settings, errors=errors)
    save_settings(settings)
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.post("/settings/test", response_class=HTMLResponse)
async def settings_test(request: Request) -> HTMLResponse:
    """Test the submitted provider settings without saving them."""
    form = await request.form()
    settings, _ = _form_to_settings(form, load_settings())
    result = await test_connection(settings)
    # HTMX swaps a fragment; a plain form post re-renders the whole page.
    if request.headers.get("hx-request", "").lower() == "true":
        ok, message = result
        return templates.TemplateResponse(
            request, "partials/test_result.html", {"ok": ok, "message": message}
        )
    return _render_settings(request, settings, test=result)

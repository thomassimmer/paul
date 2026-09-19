"""Dashboard and health check."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.config import load_settings
from app.profiler import service, store
from app.web.templating import render

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    try:
        profile = store.load_profile()
    except store.ProfileError:
        profile = None
    settings = load_settings()
    return render(
        request,
        "dashboard.html",
        active="home",
        settings=settings,
        configured=bool(settings.model),
        summary=service.summary(profile),
    )

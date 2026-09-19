"""Dashboard and health check."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.config import load_settings
from app.offers import store as offers_store
from app.profiler import service, store
from app.ranking import store as ranking_store
from app.tracker import service as tracker_service
from app.tracker import store as tracker_store
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
        offers_count=offers_store.count_offers(),
        ranked_count=len(ranking_store.list_rankings()),
        tracker=tracker_service.board_summary(
            tracker_service.tracker_rows(
                offers_store.list_offers(),
                ranking_store.list_rankings(),
                tracker_store.list_applications(),
                settings,
                tracker_service.today_utc(),
            )
        ),
    )

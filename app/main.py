"""Composition root.

Wires the FastAPI app, the shared lifespan, and one router per area. Routes
themselves live next to the feature they serve (``app/profiler/router.py``,
``app/web/routes/``), so this file stays a table of contents.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import db
from app.config import DATA_DIR
from app.offers import router as offers_router
from app.profiler import router as profiler_router
from app.ranking import router as ranking_router
from app.templates_engine import router as templates_router
from app.tracker import router as tracker_router
from app.web import WEB_DIR
from app.web.routes import board, settings
from app.web.security import OriginGuard
from app.writer import router as writer_router

ROUTERS = (
    board.router,
    settings.router,
    profiler_router.router,
    offers_router.router,
    ranking_router.router,
    tracker_router.router,
    templates_router.router,
    writer_router.router,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db()
    yield


app = FastAPI(title="Paul — apply more, apply better", lifespan=lifespan)
# The UI has no authentication: what keeps it safe is the loopback bind, and this
# is the part of that promise the socket cannot keep on its own. Added first, so it
# runs before anything else and refuses a request no route has a chance to answer.
app.add_middleware(OriginGuard)
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

for router in ROUTERS:
    app.include_router(router)

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
from app.profiler import router as profiler_router
from app.web import WEB_DIR
from app.web.routes import dashboard, settings

ROUTERS = (
    dashboard.router,
    settings.router,
    profiler_router.router,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db()
    yield


app = FastAPI(title="Paul (Emploi)", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

for router in ROUTERS:
    app.include_router(router)

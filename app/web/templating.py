"""Shared Jinja setup and the HTTP helpers every route uses.

``render`` injects the pending flash messages and clears them; ``redirect``
implements Post/Redirect/Get with one optional message; ``local_url`` reads a
``next`` field without letting a form redirect the user off the site. Keeping all
three here means no route has to think about cookies or open redirects.
"""

from __future__ import annotations

import json

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.web import WEB_DIR

templates = Jinja2Templates(directory=WEB_DIR / "templates")

FLASH_COOKIE = "paul_flash"


def _read_flashes(request: Request) -> list[dict[str, str]]:
    raw = request.cookies.get(FLASH_COOKIE)
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    flashes: list[dict[str, str]] = []
    for item in payload:
        if isinstance(item, dict):
            level = str(item.get("level", "ok"))
            message = str(item.get("message", ""))
            if message:
                flashes.append({"level": level, "message": message})
    return flashes


def render(request: Request, template: str, *, status_code: int = 200, **context: object) -> HTMLResponse:
    flashes = _read_flashes(request)
    response = templates.TemplateResponse(
        request, template, {**context, "flashes": flashes}, status_code=status_code
    )
    if flashes:
        response.delete_cookie(FLASH_COOKIE)
    return response


def redirect(url: str, *, message: str = "", level: str = "ok") -> RedirectResponse:
    """303 to ``url``, optionally carrying a one-shot flash message."""
    response = RedirectResponse(url, status_code=303)
    if message:
        response.set_cookie(
            FLASH_COOKIE,
            json.dumps([{"level": level, "message": message}]),
            max_age=60,
            httponly=True,
            samesite="lax",
        )
    return response


def local_url(value: object, default: str) -> str:
    """A ``next`` field a form sent, when it stays inside this site.

    A bare path is ours; ``//host`` is another site that would look local at a
glance, so it is refused. Anything else falls back to ``default``.
    """
    url = str(value or "")
    if url.startswith("/") and not url.startswith("//"):
        return url
    return default

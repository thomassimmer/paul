"""Refusing the requests a browser can be made to send on someone else's behalf.

The web UI has no authentication, and the README is honest about what keeps that
safe: the port is published on loopback only. Two attacks break that reasoning,
and neither is stopped by the bind address, so both are closed here by reading the
request rather than trusting the socket.

- **A page you visit while Paul is open.** A browser sends a cross-site form post
  without asking anything first, and reading the answer is not the point: rewriting
  ``api_base`` in Settings and then pressing *Test* is enough to send the stored API
  key — and the profile and offer text that follow the next call — to a server of
  the attacker's choosing. An unsafe method therefore has to carry an ``Origin``
  (or, failing that, a ``Referer``) that names this app.
- **DNS rebinding.** A hostname that resolves to 127.0.0.1 makes the browser treat
  the app as same-origin, so ``Origin`` and ``Host`` agree and the check above
  passes. What gives it away is the ``Host`` itself: a request arriving under a name
  that is not loopback is not one the README promised to answer.

A request from a tool — curl, the test suite, the Docker healthcheck — carries no
``Origin`` and a loopback ``Host``, and is let through. What is checked is what a
browser can be made to do, not who the client says it is.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from app.config import ALLOWED_HOSTS

# A cross-site one of these changes nothing, so it is the browser's business.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

BAD_HOST_STATUS = 400
BAD_ORIGIN_STATUS = 403

BAD_HOST = (
    "Invalid host header. Paul answers on 127.0.0.1, localhost and ::1; "
    "declare another name in PAUL_ALLOWED_HOSTS."
)
BAD_ORIGIN = "Cross-origin request refused. Open Paul on 127.0.0.1 or localhost, then try again."


def hostname(value: str) -> str:
    """The host name of a ``Host`` header or of an absolute URL, lower-cased.

    ``urlsplit`` is what drops the port and the brackets of an IPv6 literal, which
    is exactly the comparison wanted here: the port an operator chose says nothing
    about whether the request is ours.
    """
    try:
        return (urlsplit(value if "://" in value else f"//{value}").hostname or "").lower()
    except ValueError:  # a malformed IPv6 literal, or worse
        return ""


def refusal(method: str, headers: Mapping[str, str]) -> tuple[int, str] | None:
    """``(status, message)`` for a request that must not be served, else ``None``.

    Takes the method and the headers rather than a ``Request`` so the decision can
    be read — and tested — without building one.
    """
    host = headers.get("host", "")
    name = hostname(host)
    if not name or name not in ALLOWED_HOSTS:
        return BAD_HOST_STATUS, BAD_HOST

    if method.upper() in SAFE_METHODS:
        return None

    # A browser sends ``Origin`` on every cross-site post, and on a same-site one
    # too. When it is missing, the ``Referer`` is the next best thing; when both are
    # missing the request did not come from a page this has to defend against.
    origin = headers.get("origin", "") or headers.get("referer", "")
    if not origin:
        return None

    origin_name = hostname(origin)
    if origin_name and (origin_name == name or origin_name in ALLOWED_HOSTS):
        return None
    return BAD_ORIGIN_STATUS, BAD_ORIGIN


class OriginGuard(BaseHTTPMiddleware):
    """Apply ``refusal`` to every request, before any route sees it."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        refused = refusal(request.method, request.headers)
        if refused is not None:
            status, message = refused
            return PlainTextResponse(message, status_code=status)
        return await call_next(request)

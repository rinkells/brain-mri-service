"""Protection against web pages on other sites: the Origin checks and security headers."""

from collections.abc import Iterable

from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import cookie_parser
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.schemas import ErrorResponse

# Sent with every response, errors included.
SECURITY_HEADERS = {
    # Never guess a content type: JSON stays JSON, even if it holds markup.
    "X-Content-Type-Options": "nosniff",
    # Do not tell other sites which page of the service a link was followed from.
    "Referrer-Policy": "no-referrer",
    # No page may show the API or Swagger UI in a frame (clickjacking).
    "X-Frame-Options": "DENY",
}
# Responses with a user's data; neither the browser nor a proxy keeps them.
NO_STORE = {"Cache-Control": "no-store"}
AUTH_PREFIX = "/auth/"
# Liveness and readiness. The container healthchecks ask them, so neither is rate limited
# nor checked for cross-site requests, and neither needs a session. /health reveals
# nothing beyond what the UI shows. /health/ready tells whether the database is up; its
# load is bounded by the 2 s cache and the single live check (app.readiness), and it is
# reachable only inside Compose and on port 8000, never through nginx (port 8080).
HEALTH_PATH = "/health"
READY_PATH = "/health/ready"
HEALTH_PATHS = frozenset({HEALTH_PATH, READY_PATH})


def is_history_path(path: str) -> bool:
    """/predictions, /predictions/{id} and /stats, but not /predict."""
    return path in ("/predictions", "/stats") or path.startswith("/predictions/")


def session_cookie_name(scope: Scope) -> str:
    """SESSION_COOKIE_NAME, read from the app on each request (tests change it)."""
    return scope["app"].state.settings.session_cookie_name


def session_cookie(scope: Scope) -> str | None:
    """The value of the session cookie the request carries; None when it has none (an
    empty value counts as none)."""
    header = Headers(scope=scope).get("cookie")
    if not header:
        return None
    return cookie_parser(header).get(session_cookie_name(scope)) or None


def holds_user_data(scope: Scope) -> bool:
    """The history, everything under /auth/, and any answer to a request that carries
    the session cookie (e.g. a signed-in /predict, whose record is the user's)."""
    path = scope["path"]
    return (
        is_history_path(path)
        or path.startswith(AUTH_PREFIX)
        or session_cookie(scope) is not None
    )


class SecurityHeadersMiddleware:
    """Add SECURITY_HEADERS to every response, and Cache-Control: no-store to those that
    hold a user's data."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        added = SECURITY_HEADERS | (NO_STORE if holds_user_data(scope) else {})

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in added.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)

FORBIDDEN_ORIGIN_DETAIL = (
    "Запити з цього джерела (Origin) заборонено. Дозволені джерела задає ALLOWED_ORIGINS."
)
MISSING_ORIGIN_DETAIL = (
    "Запити, що змінюють дані й надсилають cookie сесії, мають містити заголовок Origin зі "
    "списку ALLOWED_ORIGINS."
)
STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# 403 in the API description: every route but /health and /health/ready can get it from
# the cross-site check, and routes that change data from the Origin checks too.
FORBIDDEN_READ_RESPONSE = {
    403: {
        "model": ErrorResponse,
        "description": (
            "Запит зі сторінки іншого сайту: Sec-Fetch-Site дорівнює cross-site чи same-site, "
            "а Origin не входить до ALLOWED_ORIGINS"
        ),
    }
}
FORBIDDEN_WRITE_RESPONSE = {
    403: {
        "model": ErrorResponse,
        "description": (
            "Origin не входить до ALLOWED_ORIGINS; або запит надсилає cookie сесії без "
            "заголовка Origin (захист від CSRF); або це запит зі сторінки іншого сайту "
            "(Sec-Fetch-Site)"
        ),
    }
}


class OriginCheckMiddleware:
    """Answer 403 to POST, PUT, PATCH and DELETE whose Origin is not allowed, and to
    those that carry the session cookie without an Origin at all (CSRF).

    Browsers add Origin to every such request, the same-origin ones included, so a page
    on another site cannot send a scan or delete the history through the user's browser.
    Clients that are not browsers (curl, scripts) send no Origin and are let through as
    long as they send no session cookie: without it a request can change nothing of a
    user's. With the cookie it acts for the user, so an allowed Origin is required: the
    cookie's SameSite=Lax keeps it off cross-site requests, but a page on another port
    of the same host (localhost:5173 and localhost:8080) is the same site.
    """

    def __init__(self, app: ASGIApp, allowed_origins: Iterable[str]) -> None:
        self.app = app
        self.allowed_origins = frozenset(origin.lower() for origin in allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in STATE_CHANGING_METHODS:
            origin = Headers(scope=scope).get("origin")
            detail = None
            if origin is None:
                if session_cookie(scope) is not None:
                    detail = MISSING_ORIGIN_DETAIL
            elif origin.lower() not in self.allowed_origins:
                detail = FORBIDDEN_ORIGIN_DETAIL
            if detail is not None:
                response = JSONResponse({"detail": detail}, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


CROSS_SITE_DETAIL = (
    "Запити зі сторінок інших сайтів заборонено. Дозволені джерела задає ALLOWED_ORIGINS."
)
# Sec-Fetch-Site values of requests made by a page of another site.
FOREIGN_SITES = frozenset({"cross-site", "same-site"})
# Never checked: see HEALTH_PATHS.
UNCHECKED_PATHS = HEALTH_PATHS


class CrossSiteCheckMiddleware:
    """Answer 403 to any request a page of another site makes through the browser.

    Browsers mark every request with Sec-Fetch-Site. "cross-site" and "same-site" mean
    another page made it, e.g. <img src="http://localhost:8080/stats"> or a no-cors
    fetch; such a GET passes the Origin check (which covers writes only) and would
    otherwise use up the client's rate limit. It is let through only from an Origin in
    ALLOWED_ORIGINS. "same-origin", "none" (typed in the address bar) and requests
    without the header (curl, scripts, tests) pass. Runs after the Origin check and
    before the rate limit, so a refused request counts for nothing.
    """

    def __init__(self, app: ASGIApp, allowed_origins: Iterable[str]) -> None:
        self.app = app
        self.allowed_origins = frozenset(origin.lower() for origin in allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] not in UNCHECKED_PATHS:
            headers = Headers(scope=scope)
            site = (headers.get("sec-fetch-site") or "").strip().lower()
            origin = headers.get("origin")
            allowed = origin is not None and origin.lower() in self.allowed_origins
            if site in FOREIGN_SITES and not allowed:
                response = JSONResponse({"detail": CROSS_SITE_DETAIL}, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)

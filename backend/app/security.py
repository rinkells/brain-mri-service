"""Protection against web pages on other sites: the Origin check and security headers."""

from collections.abc import Iterable

from fastapi.responses import JSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Sent with every response, errors included.
SECURITY_HEADERS = {
    # Never guess a content type: JSON stays JSON, even if it holds markup.
    "X-Content-Type-Options": "nosniff",
    # Do not tell other sites which page of the service a link was followed from.
    "Referrer-Policy": "no-referrer",
    # No page may show the API or Swagger UI in a frame (clickjacking).
    "X-Frame-Options": "DENY",
}
# History responses hold prediction results; neither the browser nor a proxy keeps them.
NO_STORE = {"Cache-Control": "no-store"}


def is_history_path(path: str) -> bool:
    """/predictions, /predictions/{id} and /stats, but not /predict."""
    return path in ("/predictions", "/stats") or path.startswith("/predictions/")


class SecurityHeadersMiddleware:
    """Add SECURITY_HEADERS to every response and Cache-Control: no-store to the history."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        added = SECURITY_HEADERS | (NO_STORE if is_history_path(scope["path"]) else {})

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
STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class OriginCheckMiddleware:
    """Answer 403 to POST, PUT, PATCH and DELETE whose Origin is not allowed.

    Browsers add Origin to every such request, the same-origin ones included, so
    a page on another site cannot send a scan or delete the history through the
    user's browser. Clients that are not browsers (curl, scripts) send no Origin
    and are let through: they are not the threat this check is for.
    """

    def __init__(self, app: ASGIApp, allowed_origins: Iterable[str]) -> None:
        self.app = app
        self.allowed_origins = frozenset(origin.lower() for origin in allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in STATE_CHANGING_METHODS:
            origin = Headers(scope=scope).get("origin")
            if origin is not None and origin.lower() not in self.allowed_origins:
                response = JSONResponse({"detail": FORBIDDEN_ORIGIN_DETAIL}, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)

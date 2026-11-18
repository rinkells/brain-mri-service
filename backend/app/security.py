"""Protection against requests that web pages on other sites make on the user's behalf."""

from collections.abc import Iterable

from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

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

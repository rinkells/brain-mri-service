"""Find the signed-in user of a request before its body is read."""

import logging
import time

from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth import (
    NOT_SIGNED_IN,
    OPTIONAL_SESSION_PATHS,
    SESSION_EXPIRED,
    SESSION_USER,
    SessionUser,
    clear_session_cookie,
    find_session_user,
    is_well_formed_token,
    needs_session,
)
from app.database import Database
from app.errors import DATABASE_NOT_CONFIGURED, error_response
from app.history import SAVE_RETRY_AFTER_S
from app.history_api import DATABASE_UNAVAILABLE, HISTORY_DISABLED
from app.security import session_cookie

logger = logging.getLogger("uvicorn.error")


class SessionMiddleware:
    """Put the signed-in user (or None) into the scope, or refuse the request.

    It runs after the Host and Origin checks and the rate limit, and before the body is
    read, so a request without a valid session never uploads a scan or reaches a handler:

    - the history and /auth/me need a session: without the cookie 401, with a cookie
      that names no unexpired session 401 that also clears the cookie, and 503 when
      there is no database (DATABASE_URL unset) or it cannot be reached;
    - /predict works without a session (anonymous, not saved). A bad cookie gets 401
      as above, so a user who believes to be signed in learns that the result would not
      be saved. When the database cannot be reached the request goes on as anonymous,
      as saving would fail too, and the database is not tried again for
      SAVE_RETRY_AFTER_S seconds (as in app.history.save_prediction).

    Other paths pass untouched; /auth/login and /auth/register read the cookie only to
    replace the session it names. State is read from app.state on each request.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        required = needs_session(path)
        if not required and path not in OPTIONAL_SESSION_PATHS:
            await self.app(scope, receive, send)
            return

        database: Database | None = scope["app"].state.database
        token = session_cookie(scope)
        user: SessionUser | None = None
        if required:
            if database is None:
                await self._refuse(
                    scope, receive, send, 503, HISTORY_DISABLED, code=DATABASE_NOT_CONFIGURED
                )
                return
            if token is None:
                await self._refuse(scope, receive, send, 401, NOT_SIGNED_IN)
                return
            try:
                user = await self._find(database, token)
            except (OperationalError, PoolTimeoutError) as exc:
                logger.error(
                    "History database unavailable on %s %s: %s", scope["method"], path, exc
                )
                await self._refuse(scope, receive, send, 503, DATABASE_UNAVAILABLE)
                return
            if user is None:
                await self._refuse(scope, receive, send, 401, SESSION_EXPIRED, clear=True)
                return
        elif token is not None and database is not None:
            if time.monotonic() < database.saving_paused_until:
                logger.warning(
                    "Prediction not saved: the history database failed less than %.0f s ago",
                    SAVE_RETRY_AFTER_S,
                )
            else:
                try:
                    user = await self._find(database, token)
                except (OperationalError, PoolTimeoutError):
                    database.saving_paused_until = time.monotonic() + SAVE_RETRY_AFTER_S
                    logger.exception(
                        "Session not checked: the history database is unavailable; the "
                        "prediction is answered but not saved"
                    )
                else:
                    if user is None:
                        await self._refuse(scope, receive, send, 401, SESSION_EXPIRED, clear=True)
                        return

        scope[SESSION_USER] = user
        await self.app(scope, receive, send)

    @staticmethod
    async def _find(database: Database, token: str) -> SessionUser | None:
        if not is_well_formed_token(token):
            return None
        return await run_in_threadpool(find_session_user, database, token)

    @staticmethod
    async def _refuse(
        scope: Scope,
        receive: Receive,
        send: Send,
        status_code: int,
        detail: str,
        clear: bool = False,
        code: str | None = None,
    ) -> None:
        response = error_response(status_code, detail, code)
        if clear:
            clear_session_cookie(response, scope["app"].state.settings)
        await response(scope, receive, send)

"""Stable codes in error bodies, for the cases the interface must tell apart.

An error body is {"detail": "..."}: the text is for people and may change. Where a
client has to react differently to errors with the same status, the body also holds
"code", which does not change. Bodies of all other errors stay as they were.
"""

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

# 503: no DATABASE_URL, so there are no accounts and no history at all (not a passing
# outage: asking again will not help).
DATABASE_NOT_CONFIGURED = "database_not_configured"
# 429 from sign-in or account deletion: too many wrong passwords for this name from
# this address (LOGIN_FAILURES_*).
LOGIN_LOCKED = "login_locked"
# 429: too many requests from this address in the last minute (RATE_LIMIT_*).
RATE_LIMITED = "rate_limited"
# 403 from DELETE /auth/me: the password is wrong (the Origin checks answer 403 too).
WRONG_PASSWORD = "wrong_password"
# 413 from /auth/*: the body is over 8 KiB.
BODY_TOO_LARGE = "body_too_large"

CODE_DESCRIPTION = (
    "Сталий код для випадків, які клієнт має розрізняти (текст detail може змінюватися): "
    f"{DATABASE_NOT_CONFIGURED} (503: не задано DATABASE_URL, облікових записів та історії "
    f"немає), {LOGIN_LOCKED} (429: забагато неправильних паролів для імені з цієї адреси), "
    f"{RATE_LIMITED} (429: забагато запитів з цієї адреси за хвилину), {WRONG_PASSWORD} "
    f"(403 від DELETE /auth/me: неправильний пароль), {BODY_TOO_LARGE} (413: тіло запиту до "
    "/auth/* більше за 8 КіБ). В інших помилках поля немає"
)


def error_body(detail: str, code: str | None = None) -> dict[str, str]:
    """{"detail": ...}, with "code" only when there is one."""
    return {"detail": detail} if code is None else {"detail": detail, "code": code}


def error_response(
    status_code: int, detail: str, code: str | None = None, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(error_body(detail, code), status_code=status_code, headers=headers)


class CodedHTTPException(HTTPException):
    """An HTTPException whose body also carries a stable code."""

    def __init__(
        self, status_code: int, detail: str, code: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code


async def coded_http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, CodedHTTPException)
    return error_response(exc.status_code, str(exc.detail), exc.code, exc.headers)

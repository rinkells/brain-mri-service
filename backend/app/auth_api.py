"""Account endpoints: register, sign in and out, and the signed-in user's own account.

There is no e-mail: no address is stored, so there is no password reset and nothing
to confirm. Every response here has Cache-Control: no-store (app.security).
"""

import logging

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.auth import (
    UNAUTHORIZED_RESPONSE,
    CurrentUser,
    Passwords,
    SessionUser,
    clear_session_cookie,
    create_user,
    credentials_problem,
    delete_user,
    end_session,
    find_user_by_name,
    password_hash_of,
    replace_password_hash,
    set_session_cookie,
    start_session,
    username_taken,
)
from app.database import Database
from app.history_api import HISTORY_DISABLED, UNAVAILABLE_RESPONSE
from app.rate_limit import (
    rate_limited_response,
    SlidingWindowLimiter,
    login_failure_key,
    rate_limit_key,
    retry_after_seconds,
    too_many_failures_detail,
)
from app.schemas import CredentialsRequest, DeleteAccountRequest, ErrorResponse, UserResponse
from app.security import FORBIDDEN_READ_RESPONSE, FORBIDDEN_WRITE_RESPONSE, session_cookie

logger = logging.getLogger("uvicorn.error")

# The same answer whether the name is unknown or the password is wrong.
LOGIN_FAILED = "Неправильне ім'я користувача або пароль."
USERNAME_TAKEN = "Це ім'я користувача вже зайняте."
PASSWORD_MISMATCH = "Неправильний пароль."
CREDENTIALS_BODY = "Тіло запиту має бути JSON-об'єктом із текстовими полями username і password."
PASSWORD_BODY = "Тіло запиту має бути JSON-об'єктом із текстовим полем password."

# A body FastAPI cannot parse gets one of these instead of its list of errors: that
# list repeats the values it was given, and they may hold a password.
BODY_DETAILS = {
    ("POST", "/auth/register"): CREDENTIALS_BODY,
    ("POST", "/auth/login"): CREDENTIALS_BODY,
    ("DELETE", "/auth/me"): PASSWORD_BODY,
}

INVALID_CREDENTIALS_RESPONSE = {
    422: {
        "model": ErrorResponse,
        "description": (
            "Тіло не є JSON-об'єктом із текстовими username і password; для реєстрації ще й "
            "ім'я чи пароль не відповідають вимогам (текст пояснює, що саме)"
        ),
    }
}
BODY_TOO_LARGE_RESPONSE = {
    413: {"model": ErrorResponse, "description": "Тіло запиту більше за 8 КіБ"}
}
INVALID_PASSWORD_BODY_RESPONSE = {
    422: {"model": ErrorResponse, "description": "Тіло не є JSON-об'єктом із текстовим password"}
}

# Each route names the limit it counts against (app.rate_limit.bucket_of).
router = APIRouter(prefix="/auth", tags=["auth"])


async def body_validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """422 in the usual {"detail": "..."} format for the bodies of /auth/*, without
    echoing what was sent; FastAPI's own answer everywhere else."""
    detail = BODY_DETAILS.get((request.method, request.url.path))
    if detail is None:
        return await request_validation_exception_handler(request, exc)
    return JSONResponse({"detail": detail}, status_code=422)


def database_of(request: Request) -> Database:
    database: Database | None = request.app.state.database
    if database is None:
        raise HTTPException(status_code=503, detail=HISTORY_DISABLED)
    return database


def to_response(user: SessionUser) -> UserResponse:
    return UserResponse(id=user.id, username=user.username, created_at=user.created_at)


def failure_key(request: Request, username: str) -> str:
    settings = request.app.state.settings
    return login_failure_key(username, request.scope, settings.trusted_proxy_addresses)


def refuse_while_failing(request: Request, key: str) -> None:
    """429 once the name has had LOGIN_FAILURES_MAX wrong passwords from this client in
    the window, before any password is checked. Unknown names count the same way, so
    the limit tells nothing about which names exist."""
    failures: SlidingWindowLimiter = request.app.state.login_failures
    wait = failures.retry_after(key)
    if wait > 0:
        seconds = retry_after_seconds(wait)
        settings = request.app.state.settings
        logger.warning(
            "Sign-in refused for %d s: too many wrong passwords for one name from %s",
            seconds,
            rate_limit_key(request.scope, settings.trusted_proxy_addresses),
        )
        raise HTTPException(
            status_code=429,
            detail=too_many_failures_detail(seconds),
            headers={"Retry-After": str(seconds)},
        )


async def begin_session(request: Request, response: Response, user: SessionUser) -> None:
    """A new session for the user, in the cookie of the response."""
    settings = request.app.state.settings
    token = await run_in_threadpool(
        start_session,
        request.app.state.database,
        user.id,
        settings.session_ttl,
        session_cookie(request.scope),
        settings.session_max_per_user,
    )
    set_session_cookie(response, token, settings)


@router.post(
    "/register",
    status_code=201,
    response_model=UserResponse,
    responses={
        **INVALID_CREDENTIALS_RESPONSE,
        **BODY_TOO_LARGE_RESPONSE,
        **FORBIDDEN_WRITE_RESPONSE,
        **UNAVAILABLE_RESPONSE,
        **rate_limited_response("RATE_LIMIT_AUTH_PER_MINUTE"),
        409: {"model": ErrorResponse, "description": "Ім'я вже зайняте (без урахування регістру)"},
    },
    summary="Зареєструватися (й одразу увійти)",
)
async def register(request: Request, response: Response, body: CredentialsRequest) -> UserResponse:
    username, password = body.username, body.password.get_secret_value()
    problem = credentials_problem(username, password)
    if problem is not None:
        raise HTTPException(status_code=422, detail=problem)
    database = database_of(request)
    if await run_in_threadpool(username_taken, database, username):
        raise HTTPException(status_code=409, detail=USERNAME_TAKEN)

    passwords: Passwords = request.app.state.passwords
    password_hash = await passwords.hash(password)
    try:
        user = await run_in_threadpool(create_user, database, username, password_hash)
    except IntegrityError as exc:
        # Registered by another request since the check above.
        raise HTTPException(status_code=409, detail=USERNAME_TAKEN) from exc
    await begin_session(request, response, user)
    return to_response(user)


@router.post(
    "/login",
    response_model=UserResponse,
    responses={
        **INVALID_CREDENTIALS_RESPONSE,
        **BODY_TOO_LARGE_RESPONSE,
        **FORBIDDEN_WRITE_RESPONSE,
        **UNAVAILABLE_RESPONSE,
        **rate_limited_response("RATE_LIMIT_AUTH_PER_MINUTE", wrong_passwords=True),
        401: {
            "model": ErrorResponse,
            "description": "Неправильне ім'я або пароль (одна відповідь на обидва випадки)",
        },
    },
    summary="Увійти",
)
async def login(request: Request, response: Response, body: CredentialsRequest) -> UserResponse:
    database = database_of(request)
    username, password = body.username, body.password.get_secret_value()
    key = failure_key(request, username)
    refuse_while_failing(request, key)

    found = await run_in_threadpool(find_user_by_name, database, username)
    passwords: Passwords = request.app.state.passwords
    # One argon2id check either way: a dummy hash when the name is unknown.
    if not await passwords.verify(found[1] if found else None, password):
        request.app.state.login_failures.hit(key)
        raise HTTPException(status_code=401, detail=LOGIN_FAILED)
    request.app.state.login_failures.forget(key)

    user, stored_hash = found
    if passwords.needs_rehash(stored_hash):
        new_hash = await passwords.hash(password)
        await run_in_threadpool(replace_password_hash, database, user.id, new_hash)
    await begin_session(request, response, user)
    return to_response(user)


@router.post(
    "/logout",
    status_code=204,
    response_class=Response,
    responses={
        **FORBIDDEN_WRITE_RESPONSE,
        **rate_limited_response("RATE_LIMIT_WRITE_PER_MINUTE"),
    },
    summary="Вийти",
)
async def logout(request: Request) -> Response:
    """Deletes the session on the server and clears the cookie. Answers 204 with or
    without a session, as many times as asked."""
    token = session_cookie(request.scope)
    database: Database | None = request.app.state.database
    if token is not None and database is not None:
        await run_in_threadpool(end_session, database, token)
    response = Response(status_code=204)
    clear_session_cookie(response, request.app.state.settings)
    return response


@router.get(
    "/me",
    response_model=UserResponse,
    responses={
        **UNAUTHORIZED_RESPONSE,
        **FORBIDDEN_READ_RESPONSE,
        **UNAVAILABLE_RESPONSE,
        **rate_limited_response("RATE_LIMIT_READ_PER_MINUTE"),
    },
    summary="Поточний користувач",
)
async def me(user: CurrentUser) -> UserResponse:
    return to_response(user)


@router.delete(
    "/me",
    status_code=204,
    response_class=Response,
    responses={
        **UNAUTHORIZED_RESPONSE,
        **INVALID_PASSWORD_BODY_RESPONSE,
        **BODY_TOO_LARGE_RESPONSE,
        **UNAVAILABLE_RESPONSE,
        **rate_limited_response("RATE_LIMIT_WRITE_PER_MINUTE", wrong_passwords=True),
        403: {
            "model": ErrorResponse,
            "description": (
                "Неправильний пароль; або Origin не входить до ALLOWED_ORIGINS чи його немає "
                "(див. інші запити, що змінюють дані)"
            ),
        },
    },
    summary="Видалити обліковий запис",
)
async def delete_me(request: Request, user: CurrentUser, body: DeleteAccountRequest) -> Response:
    """Deletes the user with every session and every saved prediction. Needs the
    password; wrong passwords count towards the same limit as failed sign-ins."""
    database = database_of(request)
    key = failure_key(request, user.username)
    refuse_while_failing(request, key)

    stored_hash = await run_in_threadpool(password_hash_of, database, user.id)
    passwords: Passwords = request.app.state.passwords
    if not await passwords.verify(stored_hash, body.password.get_secret_value()):
        request.app.state.login_failures.hit(key)
        raise HTTPException(status_code=403, detail=PASSWORD_MISMATCH)
    request.app.state.login_failures.forget(key)

    await run_in_threadpool(delete_user, database, user.id)
    response = Response(status_code=204)
    clear_session_cookie(response, request.app.state.settings)
    return response

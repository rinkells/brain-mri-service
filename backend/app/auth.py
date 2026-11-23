"""Accounts and sessions: names, password hashing, session tokens and the session cookie.

A session token is 32 random bytes, sent to the browser in an HttpOnly cookie; the
database keeps only its SHA-256, so a copy of the database holds no working cookie.
Passwords are hashed with argon2id at the library's default parameters. Neither a
password nor a token is ever logged or returned.
"""

import hashlib
import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

import anyio
import argon2
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, func, select, update
from starlette.responses import Response

from app.config import Settings
from app.database import Database
from app.records import USERNAME_MAX_LENGTH, User, UserSession, utc_now
from app.schemas import ErrorResponse
from app.security import is_history_path

logger = logging.getLogger("uvicorn.error")

NOT_SIGNED_IN = "Потрібно увійти в обліковий запис."
SESSION_EXPIRED = "Сесія недійсна або закінчилася. Увійдіть знову."

USERNAME_MIN_LENGTH = 3
# ASCII only: lower() then means the same in Python, SQLite and Postgres, and names
# that look alike in different scripts cannot pass for each other.
USERNAME_PATTERN = re.compile(
    rf"[A-Za-z0-9_.\-]{{{USERNAME_MIN_LENGTH},{USERNAME_MAX_LENGTH}}}"
)
PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 128

BAD_USERNAME = (
    "Ім'я користувача має містити від 3 до 32 символів: латинські літери, цифри, «_», «.» "
    "або «-»."
)
BAD_PASSWORD = "Пароль має містити від 10 до 128 символів."
PASSWORD_IS_USERNAME = "Пароль не може збігатися з іменем користувача."

# The body of a /auth/* request: a name and a password fit many times over. Larger ones
# get 413 before they are read to the end, let alone parsed (app.main).
AUTH_MAX_BODY_BYTES = 8 * 1024
AUTH_BODY_TOO_LARGE = "Тіло запиту до /auth/* завелике: дозволено не більше 8 КіБ."

TOKEN_BYTES = 32
# SESSION_MAX_PER_USER; the default of start_session for callers without settings.
DEFAULT_MAX_SESSIONS = 10
# What secrets.token_urlsafe(32) gives: 43 characters of unpadded URL-safe base64.
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_\-]{43}")

# An argon2id hash at the default parameters takes ~0.1 s of CPU and 64 MiB of memory.
# At most this many run at once; more sign-ins wait in the event loop. Two keep the
# memory of the api container (see docker-compose.yml) out of reach of a burst of them.
MAX_CONCURRENT_PASSWORD_HASHES = 2

# The routes that need a signed-in user: without a valid session they answer 401 before
# their body is read (app.sessions).
ME_PATH = "/auth/me"
# /predict: a session is optional there; with one the prediction is saved for the user.
OPTIONAL_SESSION_PATHS = frozenset({"/predict"})

# Where app.sessions leaves the signed-in user (a SessionUser, or None) in the scope.
SESSION_USER = "brain_mri.session_user"

UNAUTHORIZED_RESPONSE = {
    401: {
        "model": ErrorResponse,
        "description": (
            "Немає сесії, або cookie сесії недійсна чи сесія закінчилася (тоді відповідь "
            "ще й видаляє cookie)"
        ),
    }
}


def needs_session(path: str) -> bool:
    return is_history_path(path) or path == ME_PATH


@dataclass(frozen=True)
class SessionUser:
    """The signed-in user of a request."""

    id: uuid.UUID
    username: str
    created_at: datetime


def credentials_problem(username: str, password: str) -> str | None:
    """Why a new account cannot have this name and password, or None."""
    if not USERNAME_PATTERN.fullmatch(username):
        return BAD_USERNAME
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        return BAD_PASSWORD
    if password.casefold() == username.casefold():
        return PASSWORD_IS_USERNAME
    return None


# --- Tokens and the cookie ---------------------------------------------------------------


def new_session_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def session_id_of(token: str) -> str:
    """What the database keeps instead of the token."""
    return hashlib.sha256(token.encode()).hexdigest()


def is_well_formed_token(value: str) -> bool:
    """Anything else cannot be a token the service issued: no need to ask the database."""
    return TOKEN_PATTERN.fullmatch(value) is not None


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=int(settings.session_ttl.total_seconds()),
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        # Written as given ("SameSite=Lax"); browsers accept any case.
        samesite="Lax",  # type: ignore[arg-type]
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="Lax",  # type: ignore[arg-type]
    )


# --- Passwords ----------------------------------------------------------------------------


class Passwords:
    """argon2id hashing in worker threads, never in the event loop, and at most
    MAX_CONCURRENT_PASSWORD_HASHES at a time. Made with `await Passwords.create()`."""

    def __init__(
        self,
        hasher: argon2.PasswordHasher,
        dummy_hash: str,
        max_concurrent: int = MAX_CONCURRENT_PASSWORD_HASHES,
    ) -> None:
        self._hasher = hasher
        # Checked when the name belongs to nobody, so that answer takes as long as a
        # wrong password does. Same parameters as every new hash.
        self._dummy_hash = dummy_hash
        self._limiter = anyio.CapacityLimiter(max_concurrent)

    @classmethod
    async def create(cls, max_concurrent: int = MAX_CONCURRENT_PASSWORD_HASHES) -> "Passwords":
        """The dummy hash, too, is computed in a worker thread (~0.1 s, once)."""
        hasher = argon2.PasswordHasher()
        dummy_hash = await anyio.to_thread.run_sync(
            hasher.hash, secrets.token_urlsafe(TOKEN_BYTES)
        )
        return cls(hasher, dummy_hash, max_concurrent)

    async def hash(self, password: str) -> str:
        return await anyio.to_thread.run_sync(self._hasher.hash, password, limiter=self._limiter)

    async def verify(self, password_hash: str | None, password: str) -> bool:
        """Whether the password matches. None (no such user) checks the dummy hash, which
        costs the same, and is always False."""
        checked = self._dummy_hash if password_hash is None else password_hash
        matches = await anyio.to_thread.run_sync(
            self._matches, checked, password, limiter=self._limiter
        )
        return matches and password_hash is not None

    def needs_rehash(self, password_hash: str) -> bool:
        """True when the hash was made with other parameters than the current defaults."""
        return self._hasher.check_needs_rehash(password_hash)

    def _matches(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except VerifyMismatchError:
            return False
        except (VerificationError, InvalidHashError):
            # A stored value that is not a valid hash: nobody can sign in with it.
            logger.warning("A stored password hash could not be checked; sign-in refused")
            return False


# --- Users and sessions in the database (blocking: run them in worker threads) ---------


def _same_name(username: str):
    return func.lower(User.username) == username.lower()


def _session_user(user: User) -> SessionUser:
    return SessionUser(id=user.id, username=user.username, created_at=user.created_at)


def username_taken(database: Database, username: str) -> bool:
    with database.session() as session:
        return session.scalar(select(User.id).where(_same_name(username))) is not None


def create_user(database: Database, username: str, password_hash: str) -> SessionUser:
    """Raises IntegrityError when the name is taken without regard to case."""
    with database.session() as session:
        user = User(username=username, password_hash=password_hash, created_at=utc_now())
        session.add(user)
        session.commit()
        return _session_user(user)


def find_user_by_name(database: Database, username: str) -> tuple[SessionUser, str] | None:
    """The user with this name, without regard to case, and its password hash."""
    with database.session() as session:
        user = session.scalar(select(User).where(_same_name(username)))
        return (_session_user(user), user.password_hash) if user is not None else None


def password_hash_of(database: Database, user_id: uuid.UUID) -> str | None:
    with database.session() as session:
        return session.scalar(select(User.password_hash).where(User.id == user_id))


def replace_password_hash(database: Database, user_id: uuid.UUID, password_hash: str) -> None:
    with database.session() as session:
        session.execute(update(User).where(User.id == user_id).values(password_hash=password_hash))
        session.commit()


def delete_user(database: Database, user_id: uuid.UUID) -> None:
    """The database deletes the user's sessions and records with it (ON DELETE CASCADE)."""
    with database.session() as session:
        session.execute(delete(User).where(User.id == user_id))
        session.commit()


def start_session(
    database: Database,
    user_id: uuid.UUID,
    ttl: timedelta,
    replaces: str | None = None,
    max_sessions: int = DEFAULT_MAX_SESSIONS,
) -> str:
    """Store a new session and return its token.

    Every sign-in gets a new token, never one the browser brought along (session
    fixation). The session that token named, if any, is deleted, and so is every
    expired session of every user. The user keeps at most `max_sessions` sessions:
    the oldest beyond that are deleted, so signing in again and again cannot pile them up.
    """
    token = new_session_token()
    now = utc_now()
    with database.session() as session:
        session.execute(delete(UserSession).where(UserSession.expires_at <= now))
        if replaces is not None and is_well_formed_token(replaces):
            session.execute(delete(UserSession).where(UserSession.id == session_id_of(replaces)))
        session.add(
            UserSession(
                id=session_id_of(token), user_id=user_id, created_at=now, expires_at=now + ttl
            )
        )
        session.flush()
        newest = (
            select(UserSession.id)
            .where(UserSession.user_id == user_id)
            .order_by(UserSession.created_at.desc(), UserSession.id.desc())
            .limit(max_sessions)
        )
        session.execute(
            delete(UserSession).where(
                UserSession.user_id == user_id, UserSession.id.not_in(newest.scalar_subquery())
            )
        )
        session.commit()
    return token


def end_session(database: Database, token: str) -> None:
    if not is_well_formed_token(token):
        return
    with database.session() as session:
        session.execute(delete(UserSession).where(UserSession.id == session_id_of(token)))
        session.commit()


def find_session_user(database: Database, token: str) -> SessionUser | None:
    """The user of an unexpired session with this token, or None."""
    if not is_well_formed_token(token):
        return None
    with database.session() as session:
        row = session.execute(
            select(User.id, User.username, User.created_at)
            .join(UserSession, UserSession.user_id == User.id)
            .where(UserSession.id == session_id_of(token), UserSession.expires_at > utc_now())
        ).first()
    return SessionUser(*row) if row is not None else None


# --- The signed-in user of a request ---------------------------------------------------------


def signed_in_user(request: Request) -> SessionUser:
    """The user app.sessions found for this request.

    The middleware already answers 401, before the body is read, on every path that
    needs_session() names; this only guards a route that was not added there.
    """
    user = request.scope.get(SESSION_USER)
    if user is None:
        raise HTTPException(status_code=401, detail=NOT_SIGNED_IN)
    return user


CurrentUser = Annotated[SessionUser, Depends(signed_in_user)]

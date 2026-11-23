"""Requests per client per minute, and failed sign-ins per name and client: refused
with 429 above the limit.

The counts live in the memory of this one process. With several worker processes or
several API containers each would count on its own; that needs a shared store
(e.g. Redis) instead.
"""

import ipaddress
import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable

from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from app.addresses import IPAddress, parse_address
from app.config import Settings
from app.schemas import ErrorResponse

WINDOW_S = 60.0
# Upper bound on the clients tracked at once, per bucket.
MAX_KEYS = 10_000
# The prefix an IPv6 client usually gets for itself: all of it counts as one client.
IPV6_PREFIX = 64
HEALTH_PATH = "/health"
# The strict auth bucket: only the requests that check a password to let someone in.
# GET /auth/me runs on every page load and is an ordinary read; POST /auth/logout and
# DELETE /auth/me are ordinary writes (DELETE /auth/me also counts wrong passwords).
AUTH_BUCKET_ROUTES = frozenset({("POST", "/auth/login"), ("POST", "/auth/register")})
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# A name longer than any valid one cannot belong to a user; only this much of it is
# kept as part of a key, so huge names cannot fill the memory.
FAILURE_KEY_NAME_LENGTH = 64


def too_many_requests_detail(seconds: int) -> str:
    return f"Забагато запитів. Спробуйте ще раз через {seconds} с."


def too_many_failures_detail(seconds: int) -> str:
    return f"Забагато невдалих спроб входу. Спробуйте ще раз через {seconds} с."


def retry_after_seconds(wait: float) -> int:
    """Whole seconds for a Retry-After header, at least 1."""
    return max(1, math.ceil(wait))


RETRY_AFTER_HEADER = {
    "Retry-After": {
        "description": "Ціле число секунд, не менше 1",
        "schema": {"type": "integer"},
    }
}
WRONG_PASSWORDS = (
    " або забагато неправильних паролів для цього імені з цієї адреси (LOGIN_FAILURES_MAX за "
    "LOGIN_FAILURES_WINDOW_MINUTES)"
)


def rate_limited_response(limit: str = "RATE_LIMIT_*", wrong_passwords: bool = False) -> dict:
    """429 in the API description, naming the limit the route counts against."""
    return {
        429: {
            "model": ErrorResponse,
            "description": (
                f"Забагато запитів з цієї адреси за останню хвилину ({limit})"
                + (WRONG_PASSWORDS if wrong_passwords else "")
                + "; заголовок Retry-After каже, через скільки секунд повторити"
            ),
            "headers": RETRY_AFTER_HEADER,
        }
    }


RATE_LIMITED_RESPONSE = rate_limited_response()


class SlidingWindowLimiter:
    """At most `limit` requests per key in any `window_s` seconds (a sliding window).

    For each key the times of its accepted requests within the window are kept, never
    more than `limit` of them, so hammering does not make a key cost more memory.
    Refused requests are not recorded. Keys whose window has passed are dropped, and at
    most `max_keys` keys are kept: when that many are tracked, the key that has been
    idle for longest is forgotten and starts afresh if it comes back.

    The clock is a parameter so tests can move time on. Used from the event loop only,
    so no lock is needed.
    """

    def __init__(
        self,
        limit: int,
        window_s: float = WINDOW_S,
        max_keys: int = MAX_KEYS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self.max_keys = max_keys
        self._clock = clock
        # Least recently active key first.
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._next_sweep = clock() + window_s

    def __len__(self) -> int:
        return len(self._hits)

    def tracked(self, key: str) -> int:
        """How many accepted requests of the key are still in its window."""
        times = self._hits.get(key)
        return len(times) if times is not None else 0

    def retry_after(self, key: str) -> float:
        """What hit() would return, without counting anything: 0 while the key is under
        its limit, otherwise the seconds until one more would be allowed."""
        times = self._hits.get(key)
        if times is None:
            return 0.0
        now = self._clock()
        self._expire(times, now)
        if len(times) >= self.limit:
            return times[0] + self.window_s - now
        return 0.0

    def forget(self, key: str) -> None:
        """Drop everything counted for the key."""
        self._hits.pop(key, None)

    def hit(self, key: str) -> float:
        """Count a request. Returns 0 when it is allowed, otherwise the seconds until
        the oldest request in the window leaves it and one more would be allowed."""
        now = self._clock()
        if now >= self._next_sweep:
            self._sweep(now)
        times = self._hits.get(key)
        if times is not None:
            self._expire(times, now)
            self._hits.move_to_end(key)
            if len(times) >= self.limit:
                return times[0] + self.window_s - now
        else:
            if len(self._hits) >= self.max_keys:
                # O(1): the key idle for longest goes. Stale keys are left to the next
                # periodic sweep, so a flood of new keys never walks the whole table.
                self._hits.popitem(last=False)
            times = self._hits[key] = deque(maxlen=self.limit)
        times.append(now)
        return 0.0

    def _expire(self, times: deque[float], now: float) -> None:
        while times and times[0] <= now - self.window_s:
            times.popleft()

    def _sweep(self, now: float) -> None:
        """Drop every key without a request in the window; done at most once per window."""
        stale = now - self.window_s
        for key in [key for key, times in self._hits.items() if not times or times[-1] <= stale]:
            del self._hits[key]
        self._next_sweep = now + self.window_s


def build_rate_limiters(
    settings: Settings, clock: Callable[[], float] = time.monotonic
) -> dict[str, SlidingWindowLimiter]:
    """One limiter per bucket, with the per-minute limits from the settings."""
    limits = {
        "predict": settings.rate_limit_predict_per_minute,
        "write": settings.rate_limit_write_per_minute,
        "read": settings.rate_limit_read_per_minute,
        "auth": settings.rate_limit_auth_per_minute,
    }
    return {name: SlidingWindowLimiter(limit, clock=clock) for name, limit in limits.items()}


def build_login_failures(
    settings: Settings, clock: Callable[[], float] = time.monotonic
) -> SlidingWindowLimiter:
    """Wrong passwords per (name, client): at most LOGIN_FAILURES_MAX within
    LOGIN_FAILURES_WINDOW_MINUTES. Only failures are counted (see app.auth_api)."""
    return SlidingWindowLimiter(
        settings.login_failures_max, window_s=settings.login_failure_window_s, clock=clock
    )


def bucket_of(method: str, path: str) -> str | None:
    """Which limit a request counts against; None for /health, which is never limited
    (the container healthchecks call it)."""
    if path == HEALTH_PATH:
        return None
    if (method, path) in AUTH_BUCKET_ROUTES:
        return "auth"
    if method == "POST" and path == "/predict":
        return "predict"
    if method in READ_METHODS:
        return "read"
    return "write"


def _address_key(address: IPAddress) -> str:
    if isinstance(address, ipaddress.IPv6Address):
        return str(ipaddress.ip_network(f"{address}/{IPV6_PREFIX}", strict=False))
    return str(address)


def rate_limit_key(scope: Scope, trusted_proxies: frozenset[IPAddress]) -> str:
    """The key a request is counted under: the client's address, signed in or not.

    The limit is checked before the session (which needs the database), so it cannot
    depend on the user. The address is the peer of the connection; only when that peer
    is one of TRUSTED_PROXIES is the client taken from its X-Real-IP header. A header
    the proxy sends malformed (nginx never does) is ignored, and the request counts
    against the proxy's own address. IPv4-mapped addresses count as IPv4, and IPv6
    clients are grouped by their /64 prefix.
    """
    client = scope.get("client")
    host = client[0] if client else None
    # Canonical first: on a dual-stack socket an IPv4 proxy connects as ::ffff:a.b.c.d.
    address = parse_address(host)
    if address is not None and address in trusted_proxies:
        forwarded = parse_address(Headers(scope=scope).get("x-real-ip"))
        if forwarded is not None:
            address = forwarded
    if address is None:
        return host or "unknown"
    return _address_key(address)


def login_failure_key(username: str, scope: Scope, trusted_proxies: frozenset[IPAddress]) -> str:
    """Failed sign-ins are counted per name (without regard to case, as names are
    unique) and client address, so guessing one user's password from one place stops
    without locking that user out everywhere."""
    name = username.lower()[:FAILURE_KEY_NAME_LENGTH]
    return f"{rate_limit_key(scope, trusted_proxies)} {name}"


class RateLimitMiddleware:
    """Answer 429 with Retry-After to a client over its limit for the request's bucket.

    It runs after the Host and Origin checks, so their refusals do not count, and
    before the body is read: every /predict attempt counts, and a refused one never
    reaches the model or the inference queue. Settings and limiters are read from
    app.state on each request, so they can be changed while the app runs (tests do).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            state = scope["app"].state
            settings: Settings = state.settings
            bucket = bucket_of(scope["method"], scope["path"])
            if settings.rate_limit_enabled and bucket is not None:
                key = rate_limit_key(scope, settings.trusted_proxy_addresses)
                wait = state.rate_limiters[bucket].hit(key)
                if wait > 0:
                    seconds = retry_after_seconds(wait)
                    response = JSONResponse(
                        {"detail": too_many_requests_detail(seconds)},
                        status_code=429,
                        headers={"Retry-After": str(seconds)},
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)

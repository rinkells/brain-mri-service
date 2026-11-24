"""Readiness (GET /health/ready): whether the database answers, checked off the event loop."""

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future

from sqlalchemy import text

from app.database import Database

logger = logging.getLogger("uvicorn.error")

# How long GET /health/ready waits for SELECT 1. Below the 5 s timeout of the Compose
# healthcheck, so the probe answers 503 itself instead of the check being cut off.
DATABASE_TIMEOUT_S = 2.0
# The path is not rate limited: a result this fresh is answered again instead of
# checking anew, and only one check is alive at a time, so a client asking in a loop
# costs one connection every 2 s at most.
RESULT_TTL_S = 2.0
CHECK_THREAD_NAME = "readiness-check"


def _describe(exc: BaseException) -> str:
    """The exception's type and message, or only its type when the message itself
    cannot be built."""
    try:
        return f"{type(exc).__name__}: {exc}"
    except Exception:
        return type(exc).__name__


class _Check:
    """One run of SELECT 1, in a daemon thread of its own."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.future: Future[bool] = Future()
        # Running from the start, so nothing can cancel it.
        self.future.set_running_or_notify_cancel()
        # The one asyncio wrapper every request waits for, through shield. Made once:
        # a wrapper per request would leave a callback on the future per request for
        # as long as the check hangs. Made in the loop that started the check.
        self.loop = asyncio.get_running_loop()
        self.waiter: asyncio.Future[bool] = asyncio.wrap_future(self.future)


class DatabaseProbe:
    """Run SELECT 1 off the event loop, with at most one check alive at a time.

    Each check opens a new connection with the probe engine of the database (see
    app.database.create_probe_engine): never one from the pool that predictions and the
    history share, so a full pool does not make a healthy database look down. The driver
    bounds each stage on its own (see create_probe_engine): connect_timeout (2 s) the
    connection, statement_timeout (1 s) SELECT 1 on a server that works,
    tcp_user_timeout (2 s) data the network stops acknowledging, TCP keepalive (about
    5 s) an answer lost to a network that went away after acknowledging SELECT 1.

    A request waits up to timeout_s (2 s) for the check and gets False when it runs
    out; the check goes on. While a check is alive no other starts, however long it
    takes: one stuck where no limit reaches (name resolution, or a server whose kernel
    still answers while the process is frozen, as with `docker pause` in the middle of
    SELECT 1) keeps every request waiting for it and answering 503, and the
    first request after it ends starts a new one. So there is never more than one check
    thread, and a result younger than ttl_s (2 s) is answered again without a check.

    Only the check itself records the state and writes the log, and only when the
    state changes: a request that times out changes nothing, so a slow but successful
    check is not reported as an outage.
    """

    def __init__(
        self,
        timeout_s: float = DATABASE_TIMEOUT_S,
        ttl_s: float = RESULT_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timeout_s = timeout_s
        self.ttl_s = ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._current: _Check | None = None
        # The database, the result and when the check that gave it ended.
        self._last: tuple[Database, bool, float] | None = None
        self._reachable: bool | None = None
        self._closed = False

    def last_result(self) -> bool | None:
        """What the latest finished check found; None before the first one."""
        return self._reachable

    async def reachable(self, database: Database) -> bool:
        with self._lock:
            if self._closed:
                return False
            last = self._last
            if last is not None and last[0] is database and self._clock() - last[2] < self.ttl_s:
                return last[1]
            check = self._current
            if check is None or check.future.done():
                check = self._current = self._start(database)
        # The API has one event loop; another one (tests with a loop of their own) cannot
        # wait for this loop's wrapper and gets one of its own.
        if check.loop is asyncio.get_running_loop():
            waiter = check.waiter
        else:
            waiter = asyncio.wrap_future(check.future)
        try:
            # shield: a request that gives up cancels only its own wait, whose callback
            # on the waiter is removed with it.
            result = await asyncio.wait_for(asyncio.shield(waiter), self.timeout_s)
        except TimeoutError:
            return False
        # A check still running for another database (only when tests swap it) says
        # nothing about this one.
        return result and check.database is database

    def close(self) -> None:
        """Start no more checks. A running one is not waited for: its thread is a
        daemon and ends when the driver gives up or the database answers."""
        with self._lock:
            self._closed = True

    def _start(self, database: Database) -> _Check:
        check = _Check(database)
        threading.Thread(
            target=self._run, args=(check,), name=CHECK_THREAD_NAME, daemon=True
        ).start()
        return check

    def _run(self, check: _Check) -> None:
        reachable = False
        try:
            try:
                with check.database.probe_engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
            except Exception as exc:  # any failure means "not ready", whatever its kind
                self._record(check.database, False, _describe(exc))
            else:
                reachable = True
                self._record(check.database, True)
        finally:
            # Always, whatever escaped above (a BaseException too), or no check would
            # ever start again.
            check.future.set_result(reachable)

    def _record(self, database: Database, reachable: bool, reason: str = "") -> None:
        with self._lock:
            previous = self._reachable
            self._reachable = reachable
            self._last = (database, reachable, self._clock())
        if reachable and previous is False:
            logger.info("Readiness: the database answers again")
        elif not reachable and previous is not False:
            logger.warning("Readiness: the database is unreachable (%s)", reason)

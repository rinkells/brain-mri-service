"""Readiness (GET /health/ready): whether the database answers, checked off the event loop."""

import asyncio
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from sqlalchemy import text

from app.database import Database

logger = logging.getLogger("uvicorn.error")

# How long GET /health/ready waits for SELECT 1. Below the 5 s timeout of the Compose
# healthcheck, so the probe answers 503 itself instead of the check being cut off.
DATABASE_TIMEOUT_S = 2.0


class DatabaseProbe:
    """Run SELECT 1 in a thread of its own, at most one at a time, with a time limit.

    The path is not rate limited, so a client could ask for it as often as it likes. A
    single worker thread, separate from the pool that runs predictions and history
    queries, keeps that from costing more than one connection attempt at a time: while
    a check is still running (e.g. a hung database, up to the 3 s connect timeout),
    further requests wait for its result instead of starting another. A request that
    has waited timeout_s gets False; the thread is left to finish on its own.

    The error goes to the log, never to the response, and once per outage, so a
    healthcheck every few seconds does not repeat it.
    """

    def __init__(self, timeout_s: float = DATABASE_TIMEOUT_S) -> None:
        self.timeout_s = timeout_s
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="readiness")
        self._lock = threading.Lock()
        self._running: tuple[Database, Future[bool]] | None = None
        self._reachable: bool | None = None
        # Whether the error of the current outage has been logged.
        self._error_logged = False

    async def reachable(self, database: Database) -> bool:
        with self._lock:
            running = self._running
            if running is None or running[1].done() or running[0] is not database:
                running = self._running = (database, self._executor.submit(self._check, database))
            future = running[1]
        try:
            # shield: a request that gives up does not cancel the check others wait for.
            return await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(future)), self.timeout_s
            )
        except TimeoutError:
            self._note(False, f"no answer within {self.timeout_s:g} s")
            return False

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _check(self, database: Database) -> bool:
        try:
            with database.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception as exc:  # any failure means "not ready", whatever its kind
            self._note(False, f"{type(exc).__name__}: {exc}", error=True)
            return False
        self._note(True)
        return True

    def _note(self, reachable: bool, reason: str = "", error: bool = False) -> None:
        """Log when the database goes away or comes back. While it is away, the first
        error is logged too: a request that timed out logs the outage before the slow
        check finishes, and the error it finishes with says why."""
        with self._lock:
            previous, self._reachable = self._reachable, reachable
            first_error = error and not self._error_logged
            self._error_logged = not reachable and (self._error_logged or error)
        if reachable:
            if previous is False:
                logger.info("Readiness: the database answers again")
        elif previous is not False or first_error:
            logger.warning("Readiness: the database is unreachable (%s)", reason)

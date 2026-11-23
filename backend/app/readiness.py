"""Readiness (GET /health/ready): whether the database answers, checked off the event loop."""

import asyncio
import logging
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future

from sqlalchemy import Engine, event, text

from app.database import Database

logger = logging.getLogger("uvicorn.error")

# How long GET /health/ready waits for SELECT 1. Below the 5 s timeout of the Compose
# healthcheck, so the probe answers 503 itself instead of the check being cut off.
DATABASE_TIMEOUT_S = 2.0
# The path is not rate limited: a result this fresh is answered again instead of
# checking anew, so a client asking in a loop costs one connection every 2 s at most.
RESULT_TTL_S = 2.0
# Every check is over this long after it started (see DatabaseProbe).
CHECK_DEADLINE_S = 3.0
CHECK_THREAD_NAME = "readiness-check"

# The check being run by the current thread, for the do_connect hook below.
_running_here = threading.local()


class _Check:
    """One run of SELECT 1, in a daemon thread of its own."""

    def __init__(self, database: Database, started: float) -> None:
        self.database = database
        self.started = started
        self.future: Future[bool] = Future()
        self.expired = False
        self._lock = threading.Lock()
        self._socket: socket.socket | None = None
        self._ended = False
        self.timer: threading.Timer | None = None

    def watch(self, fd: int) -> None:
        """Keep a duplicate of the connection's socket, so the deadline can reach it
        even while the driver waits on it; owning the duplicate also means a closed and
        reused descriptor number can never be hit by mistake."""
        original = socket.socket(fileno=fd)
        try:
            duplicate = original.dup()
        finally:
            original.detach()
        with self._lock:
            if self._ended:
                duplicate.close()
                return
            self._socket = duplicate
            if self.expired:  # connected after the deadline: cut it off at once
                self._shut_down()

    def _shut_down(self) -> None:
        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def expire(self) -> None:
        """The deadline: shut the socket down, which on Linux wakes the driver waiting on
        it with an error. (Windows does not wake a wait that way; there the check is
        only given up, see DatabaseProbe. The API runs on Linux.)"""
        with self._lock:
            if self._ended:
                return
            self.expired = True
            if self._socket is not None:
                self._shut_down()

    def end(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
        with self._lock:
            self._ended = True
            if self._socket is not None:
                self._socket.close()
                self._socket = None


def _connect_and_watch(dialect, connection_record, cargs, cparams):
    """do_connect hook of a probe engine: open the DBAPI connection as SQLAlchemy would,
    and hand its socket to the check before anything (dialect set-up queries included)
    is sent over it."""
    connection = dialect.loaded_dbapi.connect(*cargs, **cparams)
    check: _Check | None = getattr(_running_here, "check", None)
    fd = getattr(getattr(connection, "pgconn", None), "socket", None)
    if check is not None and isinstance(fd, int) and fd >= 0:
        check.watch(fd)
    return connection


class DatabaseProbe:
    """Run SELECT 1 off the event loop: one check at a time, each over in 3 s.

    Each check opens a new connection with the probe engine of the database (see
    app.database.create_probe_engine): never one from the pool that predictions and the
    history share, so a full pool does not make a healthy database look down. Every
    stage has a limit:

    - connecting (TCP, startup, authentication): connect_timeout, 2 s;
    - SELECT 1 on a server that works: statement_timeout, 1 s;
    - data the network stops acknowledging (a drop without RST): tcp_user_timeout, 2 s;
    - a server that acknowledges and never answers (e.g. `docker pause`), which none of
      the above reach: at CHECK_DEADLINE_S (3 s) after the check started, the probe shuts
      the connection's socket down and the driver gives up at once.

    So every check is over within 3 s of its start. One thing no limit reaches is name
    resolution before the TCP connection; a check stuck there is still given up at 3 s:
    the next request starts a new check, and the stuck thread, a daemon, ends when the
    resolver does and is never waited for at exit.

    A request waits up to timeout_s (2 s) for the check and gets False when it runs
    out; the check goes on. Requests that arrive while a check runs wait for it rather
    than starting another, and a result younger than ttl_s (2 s) is answered again.

    Only the check itself records the state and writes the log, and only when the
    state changes: a request that times out changes nothing, so a slow but successful
    check is not reported as an outage.
    """

    def __init__(
        self,
        timeout_s: float = DATABASE_TIMEOUT_S,
        ttl_s: float = RESULT_TTL_S,
        deadline_s: float = CHECK_DEADLINE_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timeout_s = timeout_s
        self.ttl_s = ttl_s
        self.deadline_s = deadline_s
        self._clock = clock
        self._lock = threading.Lock()
        self._current: _Check | None = None
        # The database, the result and when the check that gave it ended.
        self._last: tuple[Database, bool, float] | None = None
        self._reachable: bool | None = None

    def last_result(self) -> bool | None:
        """What the latest finished check found; None before the first one."""
        return self._reachable

    async def reachable(self, database: Database) -> bool:
        with self._lock:
            now = self._clock()
            last = self._last
            if last is not None and last[0] is database and now - last[2] < self.ttl_s:
                return last[1]
            check = self._current
            if (
                check is None
                or check.future.done()
                or check.database is not database
                or now - check.started >= self.deadline_s
            ):
                check = self._current = self._start(database, now)
        try:
            # shield: a request that gives up does not cancel the check others wait for.
            return await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(check.future)), self.timeout_s
            )
        except TimeoutError:
            return False

    def close(self) -> None:
        """Let a running check finish, waiting no longer than its deadline."""
        with self._lock:
            check = self._current
        if check is None or check.future.done():
            return
        remaining = check.started + self.deadline_s - self._clock()
        try:
            check.future.result(timeout=max(0.0, remaining) + 0.1)
        except Exception:
            pass

    def _start(self, database: Database, now: float) -> _Check:
        engine = database.probe_engine
        if isinstance(engine, Engine) and not event.contains(
            engine, "do_connect", _connect_and_watch
        ):
            event.listen(engine, "do_connect", _connect_and_watch)
        check = _Check(database, now)
        check.timer = threading.Timer(self.deadline_s, check.expire)
        check.timer.daemon = True
        thread = threading.Thread(
            target=self._run, args=(check,), name=CHECK_THREAD_NAME, daemon=True
        )
        thread.start()
        check.timer.start()
        return check

    def _run(self, check: _Check) -> None:
        _running_here.check = check
        try:
            with check.database.probe_engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception as exc:  # any failure means "not ready", whatever its kind
            reason = f"{type(exc).__name__}: {exc}"
            if check.expired:
                reason = f"no answer within {self.deadline_s:g} s ({reason})"
            self._finish(check, False, reason)
        else:
            self._finish(check, True)
        finally:
            _running_here.check = None

    def _finish(self, check: _Check, reachable: bool, reason: str = "") -> None:
        check.end()
        with self._lock:
            # A check given up at its deadline has been replaced: its late result is old.
            current = check is self._current
            previous = self._reachable
            if current:
                self._reachable = reachable
                self._last = (check.database, reachable, self._clock())
        check.future.set_result(reachable)
        if not current:
            return
        if reachable and previous is False:
            logger.info("Readiness: the database answers again")
        elif not reachable and previous is not False:
            logger.warning("Readiness: the database is unreachable (%s)", reason)

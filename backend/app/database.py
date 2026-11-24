"""Database connection for the prediction history."""

import asyncio
import threading
from collections.abc import Iterator
from concurrent.futures import Future
from contextlib import contextmanager

from sqlalchemy import Engine, MetaData, create_engine, event, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

# Fail fast when Postgres is unreachable: a prediction waits for the save.
POSTGRES_CONNECT_TIMEOUT_S = 3

# The readiness check (GET /health/ready) opens a connection of its own for each check,
# with a limit on every stage; see app.readiness for how they add up.
PROBE_CONNECT_TIMEOUT_S = 2  # TCP connection, startup and authentication (2 s is the minimum)
PROBE_STATEMENT_TIMEOUT_MS = 1000  # SELECT 1, enforced by the server
PROBE_TCP_USER_TIMEOUT_MS = 2000  # sent data left unacknowledged: a dropped network
# How its connections show in pg_stat_activity.
PROBE_APPLICATION_NAME = "brain-mri-readiness"
# How long shutdown waits for the engines to close (see Database.dispose_off_loop).
DISPOSE_TIMEOUT_S = 1.0

# Stable constraint and index names, so Alembic migrations stay predictable.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _enable_foreign_keys(dbapi_connection, connection_record) -> None:
    """SQLite ignores REFERENCES, ON DELETE CASCADE included, unless this is on."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


def create_db_engine(url: str) -> Engine:
    """Create an engine for Postgres in production or SQLite in tests.

    Errors never show the values bound to a statement (hide_parameters): they end up in
    the log, and the values include password hashes and session ids.
    """
    parsed = make_url(url)

    if parsed.get_backend_name() == "sqlite":
        # FastAPI runs sync endpoints in worker threads.
        options: dict = {"connect_args": {"check_same_thread": False}, "hide_parameters": True}
        if parsed.database in (None, "", ":memory:"):
            # One shared connection, otherwise every connection sees its own empty database.
            options["poolclass"] = StaticPool
        engine = create_engine(parsed, **options)
        event.listen(engine, "connect", _enable_foreign_keys)
        return engine

    return create_engine(
        parsed,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": POSTGRES_CONNECT_TIMEOUT_S},
    )


def create_probe_engine(url: str) -> Engine:
    """An engine for the readiness check only: outside the shared pool, so a pool that
    predictions and the history have filled never keeps the check waiting.

    NullPool: every check opens a new connection and closes it afterwards, so it never
    meets a pooled connection that died while idle, and each stage has a time limit.
    """
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        return create_engine(
            parsed,
            poolclass=NullPool,
            connect_args={"check_same_thread": False},
            hide_parameters=True,
        )
    return create_engine(
        parsed,
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={
            "connect_timeout": PROBE_CONNECT_TIMEOUT_S,
            "options": f"-c statement_timeout={PROBE_STATEMENT_TIMEOUT_MS}",
            "tcp_user_timeout": PROBE_TCP_USER_TIMEOUT_MS,
            "application_name": PROBE_APPLICATION_NAME,
        },
    )


class Database:
    def __init__(self, engine: Engine, probe_engine: Engine | None = None) -> None:
        self.engine = engine
        # What GET /health/ready connects with (see create_probe_engine). Without one, as
        # with the stand-in engines of tests, the check uses the engine itself.
        self.probe_engine = probe_engine if probe_engine is not None else engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        # time.monotonic() value until which saving is skipped after a failure
        # (see app.history.save_prediction).
        self.saving_paused_until = 0.0

    @classmethod
    def from_url(cls, url: str) -> "Database":
        return cls(create_db_engine(url), create_probe_engine(url))

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._sessions() as session:
            yield session

    def dispose(self) -> None:
        self.engine.dispose()
        if self.probe_engine is not self.engine:
            self.probe_engine.dispose()

    async def dispose_off_loop(self, timeout_s: float = DISPOSE_TIMEOUT_S) -> bool:
        """dispose() for shutdown: in a daemon thread, waited for no longer than
        timeout_s, so a connection that hangs while closing holds up neither the event
        loop nor the exit. Not asyncio.to_thread: the loop's executor is joined when the
        loop closes. False when the time ran out."""
        done: Future[None] = Future()
        done.set_running_or_notify_cancel()

        def run() -> None:
            try:
                self.dispose()
            except Exception as exc:
                done.set_exception(exc)
            else:
                done.set_result(None)

        threading.Thread(target=run, name="database-dispose", daemon=True).start()
        try:
            await asyncio.wait_for(asyncio.wrap_future(done), timeout_s)
        except TimeoutError:
            return False
        return True

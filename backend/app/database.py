"""Database connection for the prediction history."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, MetaData, create_engine, event, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Fail fast when Postgres is unreachable: a prediction waits for the save.
POSTGRES_CONNECT_TIMEOUT_S = 3

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


class Database:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        # time.monotonic() value until which saving is skipped after a failure
        # (see app.history.save_prediction).
        self.saving_paused_until = 0.0

    @classmethod
    def from_url(cls, url: str) -> "Database":
        return cls(create_db_engine(url))

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._sessions() as session:
            yield session

    def dispose(self) -> None:
        self.engine.dispose()

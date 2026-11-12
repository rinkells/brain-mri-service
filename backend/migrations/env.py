"""Alembic environment: migrates the database named by DATABASE_URL."""

from logging.config import fileConfig

from alembic import context

from app.config import get_settings
from app.database import create_db_engine
from app.records import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    # An explicit sqlalchemy.url (set by tests through the Config object) wins.
    url = config.get_main_option("sqlalchemy.url") or get_settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not set: there is no database to migrate.")
    return url


def run_migrations_offline() -> None:
    """Print the SQL instead of running it: `alembic upgrade head --sql`."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_db_engine(database_url())
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                # SQLite cannot ALTER most things; batch mode recreates the table instead.
                render_as_batch=connection.dialect.name == "sqlite",
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

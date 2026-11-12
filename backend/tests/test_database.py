import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError, StatementError

from app.database import Database, create_db_engine
from app.records import PredictionRecord
from tests.helpers import make_record

BACKEND_DIR = Path(__file__).resolve().parents[1]


def alembic_config(url: str) -> Config:
    config = Config(BACKEND_DIR / "alembic.ini")
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    return config


# --- Engine --------------------------------------------------------------------


def test_in_memory_sqlite_is_shared_between_sessions(database):
    with database.session() as session:
        session.add(make_record())
        session.commit()

    with database.session() as other_session:
        assert other_session.scalar(select(PredictionRecord.id)) is not None


def test_postgres_engine_uses_psycopg_with_timeout():
    engine = create_db_engine("postgresql+psycopg://user:secret@db.example:5432/brain_mri")

    assert engine.dialect.driver == "psycopg"
    assert engine.pool._pre_ping is True
    engine.dispose()


# --- Table ---------------------------------------------------------------------


def test_record_round_trip(database):
    record = make_record(
        probabilities={"glioma": 0.7, "meningioma": 0.1, "notumor": 0.1, "pituitary": 0.1},
        warnings=[{"code": "unexpected_image_size", "message": "Розмір знімка 640x480."}],
        image_width=640,
        image_height=480,
    )
    with database.session() as session:
        session.add(record)
        session.commit()
        record_id = record.id

    with database.session() as session:
        loaded = session.get(PredictionRecord, record_id)

    assert isinstance(loaded.id, uuid.UUID)
    assert loaded.probabilities == record.probabilities
    assert loaded.warnings[0]["message"] == "Розмір знімка 640x480."
    assert (loaded.image_width, loaded.image_height) == (640, 480)
    assert loaded.gradcam_requested is False


def test_defaults_give_uuid_and_utc_timestamp(database):
    before = datetime.now(UTC)
    with database.session() as session:
        record = make_record()
        session.add(record)
        session.commit()
        loaded = session.get(PredictionRecord, record.id)

    assert isinstance(loaded.id, uuid.UUID)
    assert loaded.created_at.tzinfo is not None
    assert loaded.created_at.utcoffset() == timedelta(0)
    assert before - timedelta(seconds=1) <= loaded.created_at <= datetime.now(UTC)


def test_other_time_zones_are_stored_as_utc(database):
    kyiv = timezone(timedelta(hours=2))
    with database.session() as session:
        record = make_record(created_at=datetime(2026, 11, 12, 14, 0, tzinfo=kyiv))
        session.add(record)
        session.commit()
        record_id = record.id

    with database.session() as session:
        loaded = session.get(PredictionRecord, record_id)

    assert loaded.created_at == datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    assert loaded.created_at.tzinfo == UTC


def test_naive_timestamps_are_rejected(database):
    with database.session() as session:
        session.add(make_record(created_at=datetime(2026, 11, 12, 12, 0)))

        with pytest.raises(StatementError, match="Naive datetimes"):
            session.commit()


@pytest.mark.parametrize(
    "fields",
    [
        {"predicted_class": "astrocytoma"},
        {"image_width": 0},
        {"latency_ms": -1.0},
    ],
)
def test_check_constraints(database, fields):
    with database.session() as session:
        session.add(make_record(**fields))

        with pytest.raises(IntegrityError):
            session.commit()


def test_no_column_can_hold_the_image_or_its_name():
    columns = set(PredictionRecord.__table__.columns.keys())

    assert columns == {
        "id",
        "created_at",
        "predicted_class",
        "probabilities",
        "image_width",
        "image_height",
        "warnings",
        "image_sha256",
        "gradcam_requested",
        "latency_ms",
    }


# --- Migrations ----------------------------------------------------------------


@pytest.fixture
def migrated_url(tmp_path):
    url = f"sqlite:///{(tmp_path / 'history.db').as_posix()}"
    command.upgrade(alembic_config(url), "head")
    return url


def test_migrations_match_the_models(migrated_url):
    engine = create_db_engine(migrated_url)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        differences = compare_metadata(context, PredictionRecord.metadata)
    engine.dispose()

    assert differences == []


def test_migrated_database_accepts_records(migrated_url):
    database = Database.from_url(migrated_url)
    with database.session() as session:
        session.add(make_record())
        session.commit()
        assert session.scalar(select(PredictionRecord.predicted_class)) == "meningioma"
    database.dispose()


def test_downgrade_removes_the_table(migrated_url):
    command.downgrade(alembic_config(migrated_url), "base")

    engine = create_db_engine(migrated_url)
    assert "predictions" not in inspect(engine).get_table_names()
    engine.dispose()


def test_offline_sql_targets_postgres(capsys):
    url = "postgresql+psycopg://user:secret@localhost:5432/brain_mri"

    command.upgrade(alembic_config(url), "head", sql=True)

    sql = capsys.readouterr().out
    assert "CREATE TABLE predictions" in sql
    assert "id UUID NOT NULL" in sql
    assert "created_at TIMESTAMP WITH TIME ZONE NOT NULL" in sql
    assert "probabilities JSONB NOT NULL" in sql

import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.schema import CreateTable

from app.database import Database, create_db_engine
from app.records import PredictionRecord
from tests.helpers import make_record

BACKEND_DIR = Path(__file__).resolve().parents[1]

CHECK_VIOLATIONS = [
    {"predicted_class": "astrocytoma"},
    {"image_width": 0},
    {"latency_ms": -1.0},
]


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


@pytest.mark.parametrize("fields", CHECK_VIOLATIONS)
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
        "model_version",
    }


# --- Migrations ----------------------------------------------------------------


def sqlite_url(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'history.db').as_posix()}"


@pytest.fixture
def migrated_url(tmp_path):
    url = sqlite_url(tmp_path)
    command.upgrade(alembic_config(url), "head")
    return url


def insert_first_schema_row(url: str, record_id: uuid.UUID) -> None:
    """Insert a record with plain SQL, naming only the columns of migration 0001."""
    engine = create_db_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO predictions (id, created_at, predicted_class, probabilities, "
                    "image_width, image_height, warnings, image_sha256, gradcam_requested, "
                    "latency_ms) VALUES (:id, '2026-11-01 12:00:00.000000', 'glioma', "
                    "'{\"glioma\": 1.0}', 512, 512, '[]', :sha, 0, 1.5)"
                ),
                {"id": record_id.hex, "sha": "0" * 64},
            )
    finally:
        engine.dispose()


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


def test_existing_records_get_the_previous_model_version(tmp_path):
    url = sqlite_url(tmp_path)
    config = alembic_config(url)
    command.upgrade(config, "0001")
    old_id = uuid.uuid4()
    insert_first_schema_row(url, old_id)

    command.upgrade(config, "head")

    database = Database.from_url(url)
    with database.session() as session:
        assert session.get(PredictionRecord, old_id).model_version == "resnet18_v2"
        new_record = make_record(model_version="resnet18_v2c")
        session.add(new_record)
        session.commit()
        assert session.get(PredictionRecord, new_record.id).model_version == "resnet18_v2c"
    database.dispose()


def test_new_records_must_name_their_model(migrated_url):
    """The server default is only for old records: without a version, an insert fails."""
    with pytest.raises(IntegrityError):
        insert_first_schema_row(migrated_url, uuid.uuid4())


@pytest.mark.parametrize("fields", CHECK_VIOLATIONS)
def test_migrated_database_keeps_check_constraints(migrated_url, fields):
    database = Database.from_url(migrated_url)
    with database.session() as session:
        session.add(make_record(**fields))

        with pytest.raises(IntegrityError):
            session.commit()
    database.dispose()


def test_downgrade_to_0001_drops_only_the_model_version(migrated_url):
    database = Database.from_url(migrated_url)
    with database.session() as session:
        session.add(make_record())
        session.commit()
    database.dispose()

    command.downgrade(alembic_config(migrated_url), "0001")

    engine = create_db_engine(migrated_url)
    columns = {column["name"] for column in inspect(engine).get_columns("predictions")}
    with engine.connect() as connection:
        count = connection.scalar(text("SELECT count(*) FROM predictions"))
    engine.dispose()
    assert "model_version" not in columns
    assert "latency_ms" in columns
    assert count == 1


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
    assert (
        "ALTER TABLE predictions ADD COLUMN model_version VARCHAR(64) "
        "DEFAULT 'resnet18_v2' NOT NULL" in sql
    )
    assert "ALTER TABLE predictions ALTER COLUMN model_version DROP DEFAULT" in sql


# --- warnings must be a JSON array ---------------------------------------------


def insert_raw_warnings(url: str, warnings_json: str) -> uuid.UUID:
    """Insert a record with plain SQL, so warnings can be anything at all."""
    record_id = uuid.uuid4()
    engine = create_db_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO predictions (id, created_at, predicted_class, probabilities, "
                    "image_width, image_height, warnings, image_sha256, gradcam_requested, "
                    "latency_ms, model_version) VALUES (:id, '2026-11-01 12:00:00.000000', "
                    "'glioma', '{\"glioma\": 1.0}', 512, 512, :warnings, :sha, 0, 1.5, 'v')"
                ),
                {"id": record_id.hex, "warnings": warnings_json, "sha": "0" * 64},
            )
    finally:
        engine.dispose()
    return record_id


def current_revision(url: str) -> str:
    engine = create_db_engine(url)
    try:
        with engine.connect() as connection:
            return connection.scalar(text("SELECT version_num FROM alembic_version"))
    finally:
        engine.dispose()


def test_migration_stops_on_warnings_that_are_not_arrays_and_changes_nothing(tmp_path):
    url = sqlite_url(tmp_path)
    config = alembic_config(url)
    command.upgrade(config, "0002")
    good = insert_raw_warnings(url, "[]")
    as_object = insert_raw_warnings(url, '{"code": "low_confidence"}')
    not_json = insert_raw_warnings(url, "not json at all")

    with pytest.raises(RuntimeError) as error:
        command.upgrade(config, "head")

    report = str(error.value)
    assert "2 records" in report
    assert str(as_object) in report and str(not_json) in report
    assert str(good) not in report
    assert "Nothing was changed" in report
    assert current_revision(url) == "0002"
    engine = create_db_engine(url)
    with engine.connect() as connection:
        rows = dict(connection.execute(text("SELECT id, warnings FROM predictions")).all())
    engine.dispose()
    assert sorted(rows.values()) == sorted(["[]", '{"code": "low_confidence"}', "not json at all"])


def test_migration_passes_when_every_warnings_value_is_an_array(tmp_path):
    url = sqlite_url(tmp_path)
    config = alembic_config(url)
    command.upgrade(config, "0002")
    insert_raw_warnings(url, "[]")
    insert_raw_warnings(url, '[{"code": "low_confidence", "message": "Низька впевненість."}]')

    command.upgrade(config, "head")

    assert current_revision(url) == "0003"


def test_offline_sql_adds_the_warnings_check_on_postgres(capsys):
    url = "postgresql+psycopg://user:secret@localhost:5432/brain_mri"

    command.upgrade(alembic_config(url), "0002:0003", sql=True)

    sql = capsys.readouterr().out
    assert (
        "ALTER TABLE predictions ADD CONSTRAINT ck_predictions_warnings_is_array "
        "CHECK (jsonb_typeof(warnings) = 'array')" in sql
    )


def test_offline_downgrade_drops_the_warnings_check_on_postgres(capsys):
    url = "postgresql+psycopg://user:secret@localhost:5432/brain_mri"

    command.downgrade(alembic_config(url), "0003:0002", sql=True)

    assert (
        "ALTER TABLE predictions DROP CONSTRAINT ck_predictions_warnings_is_array"
        in capsys.readouterr().out
    )


def test_model_declares_the_warnings_check_for_postgres_only():
    table = PredictionRecord.__table__

    postgres_ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    sqlite_ddl = str(CreateTable(table).compile(dialect=sqlite.dialect()))

    assert "ck_predictions_warnings_is_array CHECK (jsonb_typeof(warnings) = 'array')" in (
        postgres_ddl
    )
    assert "jsonb_typeof" not in sqlite_ddl

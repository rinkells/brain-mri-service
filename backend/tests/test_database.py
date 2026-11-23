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
from app.records import PredictionRecord, User, UserSession
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
        "gradcam_requested",
        "latency_ms",
        "model_version",
        "user_id",
    }


def test_sqlite_enforces_foreign_keys(database):
    """SQLite ignores REFERENCES unless told otherwise; the cascades below depend on it."""
    with database.session() as session:
        session.add(make_record(user_id=uuid.uuid4()))

        with pytest.raises(IntegrityError):
            session.commit()


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
    engine = create_db_engine(migrated_url)
    try:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO predictions (id, created_at, predicted_class, probabilities, "
                    "image_width, image_height, warnings, gradcam_requested, latency_ms) "
                    "VALUES (:id, '2026-11-01 12:00:00.000000', 'glioma', "
                    "'{\"glioma\": 1.0}', 512, 512, '[]', 0, 1.5)"
                ),
                {"id": uuid.uuid4().hex},
            )
    finally:
        engine.dispose()


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
    """Insert a record with plain SQL, so warnings can be anything at all.

    Names the columns of migrations 0002 and 0003, image_sha256 included.
    """
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

    command.upgrade(config, "0003")

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


# --- The file hash is gone (0004) ----------------------------------------------


def hash_column_and_index(url: str) -> tuple[dict | None, list[dict]]:
    """The image_sha256 column (or None) and the indexes on predictions."""
    engine = create_db_engine(url)
    try:
        inspector = inspect(engine)
        columns = {column["name"]: column for column in inspector.get_columns("predictions")}
        return columns.get("image_sha256"), inspector.get_indexes("predictions")
    finally:
        engine.dispose()


def test_upgrade_drops_the_file_hash_and_its_index_but_keeps_the_records(tmp_path):
    url = sqlite_url(tmp_path)
    config = alembic_config(url)
    command.upgrade(config, "0003")
    old_id = insert_raw_warnings(url, "[]")

    command.upgrade(config, "head")

    column, indexes = hash_column_and_index(url)
    assert column is None
    assert {index["name"] for index in indexes} == {
        "ix_predictions_created_at",
        "ix_predictions_predicted_class",
        "ix_predictions_user_id_created_at",  # added by 0005
    }
    database = Database.from_url(url)
    with database.session() as session:
        record = session.get(PredictionRecord, old_id)
        assert (record.predicted_class, record.model_version) == ("glioma", "v")
    database.dispose()


def test_downgrade_restores_the_file_hash_as_nullable_with_its_index(migrated_url):
    database = Database.from_url(migrated_url)
    with database.session() as session:
        record = make_record()
        session.add(record)
        session.commit()
        record_id = record.id
    database.dispose()

    command.downgrade(alembic_config(migrated_url), "0003")

    column, indexes = hash_column_and_index(migrated_url)
    assert column is not None
    assert column["nullable"] is True
    assert column["type"].length == 64
    assert {"name": "ix_predictions_image_sha256", "column_names": ["image_sha256"]} in [
        {"name": index["name"], "column_names": index["column_names"]} for index in indexes
    ]
    engine = create_db_engine(migrated_url)
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT id, image_sha256 FROM predictions")).all()
    engine.dispose()
    assert rows == [(record_id.hex, None)]
    assert current_revision(migrated_url) == "0003"

    # Code of that older version may again save records, now also without a hash.
    engine = create_db_engine(migrated_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO predictions (id, created_at, predicted_class, probabilities, "
                "image_width, image_height, warnings, gradcam_requested, latency_ms, "
                "model_version) VALUES (:id, '2026-11-01 12:00:00.000000', 'glioma', "
                "'{\"glioma\": 1.0}', 512, 512, '[]', 0, 1.5, 'v')"
            ),
            {"id": uuid.uuid4().hex},
        )
    engine.dispose()

    command.upgrade(alembic_config(migrated_url), "head")

    assert hash_column_and_index(migrated_url)[0] is None


def test_offline_sql_drops_and_restores_the_file_hash_on_postgres(capsys):
    url = "postgresql+psycopg://user:secret@localhost:5432/brain_mri"

    command.upgrade(alembic_config(url), "0003:0004", sql=True)
    upgrade_sql = capsys.readouterr().out
    command.downgrade(alembic_config(url), "0004:0003", sql=True)
    downgrade_sql = capsys.readouterr().out

    assert "DROP INDEX ix_predictions_image_sha256" in upgrade_sql
    assert "ALTER TABLE predictions DROP COLUMN image_sha256" in upgrade_sql
    assert "ALTER TABLE predictions ADD COLUMN image_sha256 VARCHAR(64);" in downgrade_sql
    assert (
        "CREATE INDEX ix_predictions_image_sha256 ON predictions (image_sha256)" in downgrade_sql
    )


# --- Users and sessions (0005) -------------------------------------------------------

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


def insert_0004_row(url: str) -> uuid.UUID:
    """Insert a record with plain SQL, naming only the columns of migration 0004."""
    record_id = uuid.uuid4()
    engine = create_db_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO predictions (id, created_at, predicted_class, probabilities, "
                    "image_width, image_height, warnings, gradcam_requested, latency_ms, "
                    "model_version) VALUES (:id, '2026-11-01 12:00:00.000000', 'glioma', "
                    "'{\"glioma\": 1.0}', 512, 512, '[]', 0, 1.5, 'resnet18_v2c')"
                ),
                {"id": record_id.hex},
            )
    finally:
        engine.dispose()
    return record_id


def schema_of(url: str) -> dict:
    engine = create_db_engine(url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        return {
            "tables": tables,
            "columns": {c["name"]: c for c in inspector.get_columns("predictions")},
            "indexes": {i["name"]: i["column_names"] for i in inspector.get_indexes("predictions")},
            "foreign_keys": {
                table: [
                    (fk["constrained_columns"], fk["referred_table"], fk["options"].get("ondelete"))
                    for fk in inspector.get_foreign_keys(table)
                ]
                for table in ("predictions", "sessions")
                if table in tables
            },
        }
    finally:
        engine.dispose()


def owners_of_records(url: str) -> dict[str, str | None]:
    engine = create_db_engine(url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT id, user_id FROM predictions")).all()
    finally:
        engine.dispose()
    return dict(rows)


def add_accounts(url: str) -> dict[str, uuid.UUID]:
    """Alice with two records and a session, Bob with one of each, one ownerless record."""
    database = Database.from_url(url)
    alice = User(id=uuid.uuid4(), username="alice", password_hash="$argon2id$alice")
    bob = User(id=uuid.uuid4(), username="bob", password_hash="$argon2id$bob")
    with database.session() as session:
        session.add_all([alice, bob])
        session.flush()
        session.add_all(
            [
                UserSession(id="a" * 64, user_id=alice.id, created_at=NOW, expires_at=NOW),
                UserSession(id="b" * 64, user_id=bob.id, created_at=NOW, expires_at=NOW),
                make_record(user_id=alice.id),
                make_record(user_id=alice.id),
                make_record(user_id=bob.id),
                make_record(),
            ]
        )
        session.commit()
    database.dispose()
    return {"alice": alice.id, "bob": bob.id}


def test_upgrade_to_0005_keeps_the_records_without_an_owner(tmp_path):
    url = sqlite_url(tmp_path)
    config = alembic_config(url)
    command.upgrade(config, "0004")
    old_ids = {insert_0004_row(url), insert_0004_row(url)}

    command.upgrade(config, "head")

    assert current_revision(url) == "0005"
    schema = schema_of(url)
    assert {"users", "sessions"} <= schema["tables"]
    assert schema["columns"]["user_id"]["nullable"] is True
    assert schema["indexes"]["ix_predictions_user_id_created_at"] == ["user_id", "created_at"]
    assert schema["foreign_keys"] == {
        "predictions": [(["user_id"], "users", "CASCADE")],
        "sessions": [(["user_id"], "users", "CASCADE")],
    }
    assert owners_of_records(url) == {record_id.hex: None for record_id in old_ids}
    # SQLite cannot reflect an index on an expression, so test_migrations_match_the_models
    # skips this one: its definition is read directly.
    engine = create_db_engine(url)
    with engine.connect() as connection:
        index_sql = connection.scalar(
            text("SELECT sql FROM sqlite_master WHERE name = 'uq_users_username_lower'")
        )
    engine.dispose()
    assert index_sql == "CREATE UNIQUE INDEX uq_users_username_lower ON users (lower(username))"


def test_the_migrated_database_deletes_a_users_sessions_and_records_with_the_user(
    migrated_url,
):
    ids = add_accounts(migrated_url)
    engine = create_db_engine(migrated_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": ids["alice"].hex})
        with engine.connect() as connection:
            sessions = connection.execute(text("SELECT user_id FROM sessions")).scalars().all()
    finally:
        engine.dispose()

    assert sessions == [ids["bob"].hex]
    assert sorted(owners_of_records(migrated_url).values(), key=str) == sorted(
        [ids["bob"].hex, None], key=str
    )


def test_the_migrated_database_refuses_a_name_that_differs_only_in_case(migrated_url):
    database = Database.from_url(migrated_url)
    try:
        with database.session() as session:
            session.add(User(id=uuid.uuid4(), username="Alice", password_hash="$argon2id$1"))
            session.commit()
            session.add(User(id=uuid.uuid4(), username="aLICE", password_hash="$argon2id$2"))

            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        database.dispose()


def test_downgrade_from_0005_keeps_the_records_and_drops_the_accounts(migrated_url):
    add_accounts(migrated_url)
    config = alembic_config(migrated_url)

    command.downgrade(config, "0004")

    schema = schema_of(migrated_url)
    assert current_revision(migrated_url) == "0004"
    assert not {"users", "sessions"} & schema["tables"]
    assert "user_id" not in schema["columns"]
    assert set(schema["indexes"]) == {"ix_predictions_created_at", "ix_predictions_predicted_class"}
    assert schema["foreign_keys"] == {"predictions": []}
    engine = create_db_engine(migrated_url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM predictions")) == 4
    engine.dispose()

    command.upgrade(config, "head")

    assert set(owners_of_records(migrated_url).values()) == {None}


def test_downgrade_from_0005_reports_how_many_records_lose_their_owner(migrated_url, caplog):
    add_accounts(migrated_url)

    command.downgrade(alembic_config(migrated_url), "0004")

    assert "3 records lose their owner" in caplog.text


def test_offline_sql_creates_and_drops_the_accounts_on_postgres(capsys):
    url = "postgresql+psycopg://user:secret@localhost:5432/brain_mri"

    command.upgrade(alembic_config(url), "0004:0005", sql=True)
    upgrade_sql = capsys.readouterr().out
    command.downgrade(alembic_config(url), "0005:0004", sql=True)
    downgrade_sql = capsys.readouterr().out

    assert "CREATE TABLE users" in upgrade_sql
    assert "username VARCHAR(32) NOT NULL" in upgrade_sql
    assert "CREATE UNIQUE INDEX uq_users_username_lower ON users (lower(username))" in upgrade_sql
    assert "CREATE TABLE sessions" in upgrade_sql
    assert "id VARCHAR(64) NOT NULL" in upgrade_sql
    assert "REFERENCES users (id) ON DELETE CASCADE" in upgrade_sql
    assert "ALTER TABLE predictions ADD COLUMN user_id UUID" in upgrade_sql
    assert (
        "CREATE INDEX ix_predictions_user_id_created_at ON predictions (user_id, created_at)"
        in upgrade_sql
    )
    assert "ALTER TABLE predictions DROP COLUMN user_id" in downgrade_sql
    assert "DROP TABLE sessions" in downgrade_sql
    assert "DROP TABLE users" in downgrade_sql

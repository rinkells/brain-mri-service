from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import CLASS_NAMES, EXPECTED_IMAGE_SIZE, Settings

ENV_VARS = (
    "MODEL_PATH",
    "MODEL_VERSION",
    "DEVICE",
    "MAX_UPLOAD_MB",
    "MAX_PIXELS",
    "DATABASE_URL",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def load_settings() -> Settings:
    # _env_file=None keeps a developer's local .env out of the tests.
    return Settings(_env_file=None)


def test_class_order_matches_training():
    assert CLASS_NAMES == ("glioma", "meningioma", "notumor", "pituitary")


def test_expected_image_size():
    assert EXPECTED_IMAGE_SIZE == (512, 512)


def test_defaults():
    settings = load_settings()

    assert settings.model_path == Path("models/resnet18_v2c_best.pt")
    assert settings.model_version == "resnet18_v2c"
    assert settings.device == "cpu"
    assert settings.max_upload_mb == 10
    assert settings.max_upload_bytes == 10 * 1024 * 1024
    assert settings.max_pixels == 4096 * 4096
    assert settings.database_url is None


def test_environment_variables_override_defaults(monkeypatch):
    monkeypatch.setenv("MODEL_PATH", "weights/other.pt")
    monkeypatch.setenv("MODEL_VERSION", "other_v3")
    monkeypatch.setenv("DEVICE", "cuda:0")
    monkeypatch.setenv("MAX_UPLOAD_MB", "0.5")
    monkeypatch.setenv("MAX_PIXELS", "1000000")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/brain_mri")

    settings = load_settings()

    assert settings.model_path == Path("weights/other.pt")
    assert settings.model_version == "other_v3"
    assert settings.device == "cuda:0"
    assert settings.max_upload_bytes == 512 * 1024
    assert settings.max_pixels == 1_000_000
    assert settings.database_url == "postgresql+psycopg://u:p@db:5432/brain_mri"


def test_env_file_is_read(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("DEVICE=cuda:1\nMAX_PIXELS=2500\n", encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert settings.device == "cuda:1"
    assert settings.max_pixels == 2500


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MAX_UPLOAD_MB", "0"),
        ("MAX_UPLOAD_MB", "-1"),
        ("MAX_UPLOAD_MB", "abc"),
        ("MAX_PIXELS", "0"),
        ("MAX_PIXELS", "1.5"),
        ("MAX_PIXELS", "many"),
        ("MODEL_VERSION", ""),
        ("MODEL_VERSION", "x" * 65),
    ],
)
def test_invalid_values_are_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        load_settings()

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import CLASS_NAMES, EXPECTED_IMAGE_SIZE, Settings
from app.input_check import NotMriThresholds

ENV_VARS = (
    "MODEL_PATH",
    "MODEL_VERSION",
    "DEVICE",
    "MAX_UPLOAD_MB",
    "MAX_PIXELS",
    "MAX_IMAGE_SIDE",
    "MAX_CONCURRENT_INFERENCES",
    "MAX_QUEUE",
    "LOW_CONFIDENCE_THRESHOLD",
    "DATABASE_URL",
    "ALLOWED_HOSTS",
    "ALLOWED_ORIGINS",
    "ENABLE_DOCS",
    "NOT_MRI_COLOR_MAX",
    "NOT_MRI_NOISE_MAX",
    "NOT_MRI_STD_MIN",
    "NOT_MRI_LOWCONTRAST_STD",
    "NOT_MRI_DARK_MIN",
    "NOT_MRI_OPAQUE_MIN",
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
    assert settings.low_confidence_threshold == 0.9
    assert settings.database_url is None


def test_environment_variables_override_defaults(monkeypatch):
    monkeypatch.setenv("MODEL_PATH", "weights/other.pt")
    monkeypatch.setenv("MODEL_VERSION", "other_v3")
    monkeypatch.setenv("DEVICE", "cuda:0")
    monkeypatch.setenv("MAX_UPLOAD_MB", "0.5")
    monkeypatch.setenv("MAX_PIXELS", "1000000")
    monkeypatch.setenv("LOW_CONFIDENCE_THRESHOLD", "0.75")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/brain_mri")

    settings = load_settings()

    assert settings.model_path == Path("weights/other.pt")
    assert settings.model_version == "other_v3"
    assert settings.device == "cuda:0"
    assert settings.max_upload_bytes == 512 * 1024
    assert settings.max_pixels == 1_000_000
    assert settings.low_confidence_threshold == 0.75
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
        ("LOW_CONFIDENCE_THRESHOLD", "-0.1"),
        ("LOW_CONFIDENCE_THRESHOLD", "1.5"),
        ("LOW_CONFIDENCE_THRESHOLD", "high"),
    ],
)
def test_invalid_values_are_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        load_settings()


def test_max_image_side(monkeypatch):
    assert load_settings().max_image_side == 4096

    monkeypatch.setenv("MAX_IMAGE_SIDE", "2048")
    assert load_settings().max_image_side == 2048


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "long"])
def test_invalid_max_image_side_is_rejected(monkeypatch, value):
    monkeypatch.setenv("MAX_IMAGE_SIDE", value)

    with pytest.raises(ValidationError):
        load_settings()


def test_inference_limits(monkeypatch):
    settings = load_settings()
    assert (settings.max_concurrent_inferences, settings.max_queue) == (2, 20)

    monkeypatch.setenv("MAX_CONCURRENT_INFERENCES", "4")
    monkeypatch.setenv("MAX_QUEUE", "0")
    settings = load_settings()
    assert (settings.max_concurrent_inferences, settings.max_queue) == (4, 0)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MAX_CONCURRENT_INFERENCES", "0"),
        ("MAX_CONCURRENT_INFERENCES", "-1"),
        ("MAX_CONCURRENT_INFERENCES", "two"),
        ("MAX_QUEUE", "-1"),
        ("MAX_QUEUE", "1.5"),
    ],
)
def test_invalid_inference_limits_are_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        load_settings()


def test_host_and_origin_defaults():
    settings = load_settings()

    assert settings.allowed_hosts == ["localhost", "127.0.0.1", "api"]
    assert settings.allowed_origins == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]


def test_hosts_and_origins_are_comma_separated(monkeypatch):
    monkeypatch.setenv("ALLOWED_HOSTS", "MRI.example, 10.0.0.5")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://MRI.example/, http://10.0.0.5:8080")

    settings = load_settings()

    assert settings.allowed_hosts == ["mri.example", "10.0.0.5"]
    assert settings.allowed_origins == ["https://mri.example", "http://10.0.0.5:8080"]


def test_no_origins_means_no_browser_may_write(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "")

    assert load_settings().allowed_origins == []


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ALLOWED_HOSTS", ""),
        ("ALLOWED_ORIGINS", "mri.example"),
        ("ALLOWED_ORIGINS", "https://mri.example/app"),
        ("ALLOWED_ORIGINS", "ftp://mri.example"),
        ("ALLOWED_ORIGINS", "https://mri.example?x=1"),
    ],
)
def test_invalid_hosts_and_origins_are_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        load_settings()


def test_enable_docs(monkeypatch):
    assert load_settings().enable_docs is True

    monkeypatch.setenv("ENABLE_DOCS", "false")
    assert load_settings().enable_docs is False


# --- not_mri_like thresholds ----------------------------------------------------


def test_not_mri_thresholds_default_to_the_measured_values():
    assert load_settings().not_mri_thresholds == NotMriThresholds(
        color_max=7.0,
        noise_max=0.85,
        std_min=10.0,
        lowcontrast_std=30.0,
        dark_min=0.15,
        opaque_min=0.99,
    )


def test_not_mri_thresholds_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("NOT_MRI_COLOR_MAX", "12.5")
    monkeypatch.setenv("NOT_MRI_NOISE_MAX", "0.9")
    monkeypatch.setenv("NOT_MRI_STD_MIN", "5")
    monkeypatch.setenv("NOT_MRI_LOWCONTRAST_STD", "25")
    monkeypatch.setenv("NOT_MRI_DARK_MIN", "0.2")
    monkeypatch.setenv("NOT_MRI_OPAQUE_MIN", "0.95")

    assert load_settings().not_mri_thresholds == NotMriThresholds(
        color_max=12.5,
        noise_max=0.9,
        std_min=5.0,
        lowcontrast_std=25.0,
        dark_min=0.2,
        opaque_min=0.95,
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("NOT_MRI_COLOR_MAX", "-1"),
        ("NOT_MRI_COLOR_MAX", "256"),
        ("NOT_MRI_NOISE_MAX", "-0.1"),
        ("NOT_MRI_STD_MIN", "-1"),
        ("NOT_MRI_STD_MIN", "256"),
        ("NOT_MRI_LOWCONTRAST_STD", "-1"),
        ("NOT_MRI_LOWCONTRAST_STD", "256"),
        ("NOT_MRI_DARK_MIN", "-0.1"),
        ("NOT_MRI_DARK_MIN", "1.5"),
        ("NOT_MRI_DARK_MIN", "some"),
        ("NOT_MRI_OPAQUE_MIN", "-0.1"),
        ("NOT_MRI_OPAQUE_MIN", "1.5"),
    ],
)
def test_invalid_not_mri_thresholds_are_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        load_settings()

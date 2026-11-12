"""Service configuration, read from environment variables and an optional .env file."""

from functools import lru_cache
from pathlib import Path
from typing import Literal, get_args

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ClassName = Literal["glioma", "meningioma", "notumor", "pituitary"]

# Order of the network outputs. It is fixed by the trained weights
# (alphabetical order of the dataset folders), so it is not configurable.
CLASS_NAMES: tuple[ClassName, ...] = get_args(ClassName)

# Scan size the model is reliable on: every glioma in the training data was 512x512.
EXPECTED_IMAGE_SIZE = (512, 512)


class Settings(BaseSettings):
    """Settings taken from MODEL_PATH, DEVICE, MAX_UPLOAD_MB, MAX_PIXELS and DATABASE_URL."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=(),
    )

    model_path: Path = Path("models/resnet18_v2_best.pt")
    device: str = "cpu"
    max_upload_mb: float = Field(default=10, gt=0)
    max_pixels: int = Field(default=16_777_216, gt=0)  # 4096 x 4096
    # SQLAlchemy URL of the prediction history, e.g.
    # postgresql+psycopg://user:password@localhost:5432/brain_mri.
    # Without it the service still predicts, but keeps no history.
    database_url: str | None = None

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb * 1024 * 1024)


@lru_cache
def get_settings() -> Settings:
    return Settings()

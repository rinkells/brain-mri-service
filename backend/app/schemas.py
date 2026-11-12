"""Response schemas of the API."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.config import ClassName


class HealthResponse(BaseModel):
    status: Literal["ok"]
    device: str = Field(description="Пристрій, на якому працює модель")
    classes: list[ClassName] = Field(description="Класи в порядку виходів моделі")


class ImageSize(BaseModel):
    width: int
    height: int


class PredictionWarning(BaseModel):
    code: str = Field(description="Сталий ідентифікатор попередження для програмної обробки")
    message: str = Field(description="Пояснення для людини")


class PredictionResponse(BaseModel):
    id: UUID | None = Field(
        description=(
            "Ідентифікатор запису в історії; null, якщо історію вимкнено або запис не вдалося "
            "зберегти"
        )
    )
    predicted_class: ClassName = Field(description="Клас із найбільшою ймовірністю")
    probabilities: dict[ClassName, float] = Field(description="Ймовірності всіх чотирьох класів")
    image_size: ImageSize = Field(description="Розмір завантаженого знімка в пікселях")
    warnings: list[PredictionWarning] = Field(
        description="Попередження про надійність результату; порожній список, якщо їх немає"
    )
    gradcam_png_base64: str | None = Field(
        default=None,
        description="Теплова карта Grad-CAM поверх знімка (PNG у base64); лише з ?gradcam=true",
    )


class PredictionRecordResponse(BaseModel):
    """A saved prediction. The scan itself and its file name are never stored."""

    id: UUID
    created_at: datetime = Field(description="Час прогнозу в UTC")
    predicted_class: ClassName
    probabilities: dict[ClassName, float]
    image_size: ImageSize
    warnings: list[PredictionWarning]
    image_sha256: str = Field(description="SHA-256 завантаженого файлу (hex)")
    gradcam_requested: bool = Field(description="Чи запитували теплову карту")
    latency_ms: float = Field(description="Час обробки запиту без збереження в історію, мс")


class PredictionPage(BaseModel):
    items: list[PredictionRecordResponse] = Field(description="Записи, від найновішого")
    total: int = Field(description="Кількість записів, що відповідають фільтру")
    limit: int
    offset: int


class StatsResponse(BaseModel):
    total: int = Field(description="Кількість збережених прогнозів")
    by_class: dict[ClassName, int] = Field(description="Кількість прогнозів для кожного класу")
    with_warnings_share: float | None = Field(
        description="Частка прогнозів із попередженнями (0–1); null, якщо прогнозів немає"
    )
    average_latency_ms: float | None = Field(
        description="Середній час обробки, мс; null, якщо прогнозів немає"
    )


class ErrorResponse(BaseModel):
    detail: str

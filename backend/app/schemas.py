"""Response schemas of the API."""

from typing import Literal

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


class ErrorResponse(BaseModel):
    detail: str

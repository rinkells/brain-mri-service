"""Table of the prediction history.

Only the outcome of a prediction is stored. The scan itself and the file name
are never saved: they are medical data. The SHA-256 of the file is kept so
repeated uploads of the same scan can be recognised.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, Dialect, String, TypeDecorator, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.config import CLASS_NAMES
from app.database import Base

# JSONB on Postgres (indexable, compact), plain JSON text elsewhere.
JSONType = JSON().with_variant(JSONB(), "postgresql")


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC timestamps on every database.

    Postgres keeps the time zone itself. SQLite has no time zone support, so the
    value is stored as naive UTC and marked as UTC again when it is read.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetimes are ambiguous; pass a timezone-aware value")
        value = value.astimezone(UTC)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def utc_now() -> datetime:
    return datetime.now(UTC)


_CLASS_LIST = ", ".join(f"'{name}'" for name in CLASS_NAMES)


class PredictionRecord(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        CheckConstraint(f"predicted_class IN ({_CLASS_LIST})", name="predicted_class"),
        CheckConstraint("image_width > 0 AND image_height > 0", name="image_size"),
        CheckConstraint("latency_ms >= 0", name="latency_ms"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    predicted_class: Mapped[str] = mapped_column(String(20), index=True)
    probabilities: Mapped[dict[str, float]] = mapped_column(JSONType)
    image_width: Mapped[int]
    image_height: Mapped[int]
    warnings: Mapped[list[dict[str, str]]] = mapped_column(JSONType)
    image_sha256: Mapped[str] = mapped_column(String(64), index=True)
    gradcam_requested: Mapped[bool]
    latency_ms: Mapped[float]

"""Tables: users, their sessions and the prediction history.

Only the outcome of a prediction is stored. The scan itself, the file name and
anything that identifies the file, such as its hash, are never saved: they are
medical data. A user's password is kept only as an argon2id hash, and a session
only as the SHA-256 of its token.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Dialect,
    ForeignKey,
    Index,
    String,
    TypeDecorator,
    Uuid,
    func,
)
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

# Longest user name (see app.auth for what a name may hold).
USERNAME_MAX_LENGTH = 32


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Kept as typed; unique without regard to case (index below).
    username: Mapped[str] = mapped_column(String(USERNAME_MAX_LENGTH))
    # argon2id in PHC format, e.g. $argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>.
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)

    def __repr__(self) -> str:
        # Never the password hash.
        return f"User(id={self.id}, username={self.username!r})"


# lower() only, not casefold: names are ASCII (app.auth), where the two agree.
Index("uq_users_username_lower", func.lower(User.username), unique=True)


class UserSession(Base):
    """A signed-in browser. The cookie holds a random token; only its SHA-256 is kept,
    so a copy of the database cannot be turned into working cookies."""

    __tablename__ = "sessions"

    # Hex SHA-256 of the token.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    # Expired sessions are refused, and deleted at the next sign-in of anyone.
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)

    def __repr__(self) -> str:
        # Never the id: it is a hash of the token.
        return f"UserSession(user_id={self.user_id}, expires_at={self.expires_at})"


class PredictionRecord(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        CheckConstraint(f"predicted_class IN ({_CLASS_LIST})", name="predicted_class"),
        CheckConstraint("image_width > 0 AND image_height > 0", name="image_size"),
        CheckConstraint("latency_ms >= 0", name="latency_ms"),
        # Postgres only (migration 0003): SQLite, used in tests, has no jsonb_typeof.
        CheckConstraint("jsonb_typeof(warnings) = 'array'", name="warnings_is_array").ddl_if(
            dialect="postgresql"
        ),
        # One user's history, newest first.
        Index("ix_predictions_user_id_created_at", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    predicted_class: Mapped[str] = mapped_column(String(20), index=True)
    probabilities: Mapped[dict[str, float]] = mapped_column(JSONType)
    image_width: Mapped[int]
    image_height: Mapped[int]
    warnings: Mapped[list[dict[str, str]]] = mapped_column(JSONType)
    gradcam_requested: Mapped[bool]
    latency_ms: Mapped[float]
    # MODEL_VERSION of the weights that made the prediction. There is no default:
    # every new record must name its model (older records were filled in by migration 0002).
    model_version: Mapped[str] = mapped_column(String(64))
    # The user who made the prediction. NULL for records saved before accounts existed
    # (migration 0005): those are shown to nobody. Deleting the user deletes its records.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), default=None
    )

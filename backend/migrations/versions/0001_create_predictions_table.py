"""create predictions table

Revision ID: 0001
Revises:
Create Date: 2026-11-12 23:13:15.915390
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSONB on Postgres, JSON text elsewhere (same as app.records.JSONType).
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "predictions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("predicted_class", sa.String(length=20), nullable=False),
        sa.Column("probabilities", JSON_TYPE, nullable=False),
        sa.Column("image_width", sa.Integer(), nullable=False),
        sa.Column("image_height", sa.Integer(), nullable=False),
        sa.Column("warnings", JSON_TYPE, nullable=False),
        sa.Column("image_sha256", sa.String(length=64), nullable=False),
        sa.Column("gradcam_requested", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "predicted_class IN ('glioma', 'meningioma', 'notumor', 'pituitary')",
            name=op.f("ck_predictions_predicted_class"),
        ),
        sa.CheckConstraint(
            "image_width > 0 AND image_height > 0", name=op.f("ck_predictions_image_size")
        ),
        sa.CheckConstraint("latency_ms >= 0", name=op.f("ck_predictions_latency_ms")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_predictions")),
    )
    op.create_index(op.f("ix_predictions_created_at"), "predictions", ["created_at"])
    op.create_index(op.f("ix_predictions_image_sha256"), "predictions", ["image_sha256"])
    op.create_index(op.f("ix_predictions_predicted_class"), "predictions", ["predicted_class"])


def downgrade() -> None:
    op.drop_index(op.f("ix_predictions_predicted_class"), table_name="predictions")
    op.drop_index(op.f("ix_predictions_image_sha256"), table_name="predictions")
    op.drop_index(op.f("ix_predictions_created_at"), table_name="predictions")
    op.drop_table("predictions")

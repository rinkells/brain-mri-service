"""add model_version to predictions

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-14 17:05:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every record saved before this migration was made by the first model.
PREVIOUS_MODEL_VERSION = "resnet18_v2"


def upgrade() -> None:
    # The server default fills in the existing records. It is dropped right after,
    # so a new record without a model version fails instead of being labelled
    # with the old model. Batch mode is needed for SQLite, which cannot ALTER a column.
    with op.batch_alter_table("predictions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "model_version",
                sa.String(length=64),
                nullable=False,
                server_default=PREVIOUS_MODEL_VERSION,
            )
        )
    with op.batch_alter_table("predictions") as batch_op:
        batch_op.alter_column("model_version", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("predictions") as batch_op:
        batch_op.drop_column("model_version")

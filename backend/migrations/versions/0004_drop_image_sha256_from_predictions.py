"""drop image_sha256 from predictions

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-21 16:00:00.000000

Nothing read the file hash, and it identifies the scan, so it is no longer kept.
The hashes already stored are deleted with the column; a downgrade cannot bring
them back, so it restores the column as nullable and old records get NULL.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ix_predictions_image_sha256"


def upgrade() -> None:
    # Batch mode is needed for SQLite, which recreates the table to drop a column.
    with op.batch_alter_table("predictions") as batch_op:
        batch_op.drop_index(batch_op.f(INDEX))
        batch_op.drop_column("image_sha256")


def downgrade() -> None:
    with op.batch_alter_table("predictions") as batch_op:
        batch_op.add_column(sa.Column("image_sha256", sa.String(length=64), nullable=True))
        batch_op.create_index(batch_op.f(INDEX), ["image_sha256"])

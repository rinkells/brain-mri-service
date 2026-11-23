"""add users and sessions, and the owner of each prediction

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-23 15:00:00.000000

Records saved before this migration get user_id NULL: they belong to nobody, so no
user is shown them. A user's sessions and records are deleted together with the user
(ON DELETE CASCADE).

A downgrade drops the users and their sessions, but keeps every record: the owner of
each is forgotten. Code from before this migration shows the whole history to anyone,
so the records of all users become visible to everyone who can reach the API. The
downgrade logs how many records lose their owner.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

USERNAME_INDEX = "uq_users_username_lower"
OWNER_INDEX = "ix_predictions_user_id_created_at"
OWNER_KEY = "fk_predictions_user_id_users"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    # Unique without regard to case: "Alice" and "alice" are one name.
    op.create_index(USERNAME_INDEX, "users", [sa.text("lower(username)")], unique=True)

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
    )
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"])
    op.create_index(op.f("ix_sessions_expires_at"), "sessions", ["expires_at"])

    # Batch mode is needed for SQLite, which recreates the table to add a foreign key.
    # The new column is NULL in every existing record.
    with op.batch_alter_table("predictions") as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f(OWNER_KEY), "users", ["user_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.create_index(batch_op.f(OWNER_INDEX), ["user_id", "created_at"])


def downgrade() -> None:
    if not context.is_offline_mode():
        owned = op.get_bind().scalar(
            sa.text("SELECT count(*) FROM predictions WHERE user_id IS NOT NULL")
        )
        if owned:
            logger.warning(
                "%d records lose their owner: the code before 0005 shows them to anyone",
                owned,
            )

    with op.batch_alter_table("predictions") as batch_op:
        batch_op.drop_index(batch_op.f(OWNER_INDEX))
        batch_op.drop_constraint(batch_op.f(OWNER_KEY), type_="foreignkey")
        batch_op.drop_column("user_id")

    op.drop_index(op.f("ix_sessions_expires_at"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_user_id"), table_name="sessions")
    op.drop_table("sessions")
    op.drop_index(USERNAME_INDEX, table_name="users")
    op.drop_table("users")

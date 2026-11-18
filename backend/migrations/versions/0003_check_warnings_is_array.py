"""check that warnings is a JSON array

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-18 18:00:00.000000
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_predictions_warnings_is_array"
CONDITION = "jsonb_typeof(warnings) = 'array'"

# Records whose warnings are not a JSON array. SQLite has no jsonb_typeof, and its
# JSON column is plain text that may not even be valid JSON.
NON_ARRAY_WARNINGS = {
    "postgresql": (
        "SELECT id, warnings::text FROM predictions "
        "WHERE jsonb_typeof(warnings) <> 'array' ORDER BY created_at"
    ),
    "sqlite": (
        "SELECT id, warnings FROM predictions "
        "WHERE (CASE WHEN json_valid(warnings) THEN json_type(warnings) END) IS NOT 'array' "
        "ORDER BY created_at"
    ),
}
SHOWN_RECORDS = 20
SHOWN_CHARACTERS = 200


def check_existing_records(dialect: str) -> None:
    """Stop, before anything is changed, if some records would violate the constraint.

    The records are listed so they can be fixed or deleted by hand; the migration
    never rewrites history data itself.
    """
    query = NON_ARRAY_WARNINGS.get(dialect)
    if query is None:
        return
    rows = op.get_bind().execute(sa.text(query)).all()
    if not rows:
        return
    listed = "\n".join(
        # SQLite keeps the id as 32 hex digits; show it the way the API does.
        f"  id={uuid.UUID(str(record_id))}  warnings={str(warnings)[:SHOWN_CHARACTERS]}"
        for record_id, warnings in rows[:SHOWN_RECORDS]
    )
    more = f"\n  ... and {len(rows) - SHOWN_RECORDS} more" if len(rows) > SHOWN_RECORDS else ""
    raise RuntimeError(
        f"{len(rows)} records in predictions have warnings that are not a JSON array, so "
        f"{CONSTRAINT} cannot be added. Nothing was changed. Fix or delete these records "
        f"and run the migration again:\n{listed}{more}"
    )


def upgrade() -> None:
    dialect = op.get_context().dialect.name
    # `alembic upgrade --sql` cannot read the data. Postgres still refuses the
    # constraint if a row violates it, without changing anything.
    if not context.is_offline_mode():
        check_existing_records(dialect)
    # Postgres only: SQLite (used by the tests) has no jsonb_typeof. There the API
    # copes with a malformed record instead (see app.history.well_formed_warnings).
    if dialect == "postgresql":
        op.create_check_constraint(op.f(CONSTRAINT), "predictions", CONDITION)


def downgrade() -> None:
    if op.get_context().dialect.name == "postgresql":
        op.drop_constraint(op.f(CONSTRAINT), "predictions", type_="check")

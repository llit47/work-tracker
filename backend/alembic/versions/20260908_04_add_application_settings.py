"""add application presentation settings

Revision ID: 20260908_04
Revises: 20260907_03
Create Date: 2026-09-08
"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "20260908_04"
down_revision = "20260907_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    application_settings = op.create_table(
        "application_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("application_title", sa.String(length=100), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_application_settings_singleton"),
        sa.CheckConstraint(
            "length(trim(application_title)) BETWEEN 1 AND 100",
            name="ck_application_settings_title",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "location_display_names",
        sa.Column("location", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "length(trim(display_name)) BETWEEN 1 AND 100",
            name="ck_location_display_names_value",
        ),
        sa.PrimaryKeyConstraint("location"),
    )
    op.bulk_insert(
        application_settings,
        [
            {
                "id": 1,
                "application_title": "Work Tracker",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("location_display_names")
    op.drop_table("application_settings")

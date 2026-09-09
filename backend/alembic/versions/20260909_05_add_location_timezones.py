"""add location timezones

Revision ID: 20260909_05
Revises: 20260908_04
Create Date: 2026-09-09
"""
from alembic import op
import sqlalchemy as sa

revision = "20260909_05"
down_revision = "20260908_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "location_timezones",
        sa.Column("location", sa.String(length=100), nullable=False),
        sa.Column("timezone", sa.String(length=100), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "length(trim(timezone)) BETWEEN 1 AND 100",
            name="ck_location_timezones_value",
        ),
        sa.PrimaryKeyConstraint("location"),
    )


def downgrade() -> None:
    op.drop_table("location_timezones")

"""add work event corrections

Revision ID: 20260907_02
Revises: 20260906_01
Create Date: 2026-09-07
"""
from alembic import op
import sqlalchemy as sa

revision = "20260907_02"
down_revision = "20260906_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_event_corrections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("correction_type", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.Column("raw_event_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=10), nullable=True),
        sa.Column("event_timestamp", sa.String(length=40), nullable=True),
        sa.Column("event_timestamp_utc", sa.String(length=40), nullable=True),
        sa.Column("location", sa.String(length=100), nullable=True),
        sa.CheckConstraint(
            "correction_type IN ('timestamp_override', 'ignore_event', 'manual_event')",
            name="ck_work_event_corrections_type",
        ),
        sa.CheckConstraint(
            "event_type IS NULL OR event_type IN ('entry', 'exit')",
            name="ck_work_event_corrections_event_type",
        ),
        sa.CheckConstraint(
            "(correction_type = 'timestamp_override' AND raw_event_id IS NOT NULL "
            "AND event_type IS NULL AND event_timestamp IS NOT NULL "
            "AND event_timestamp_utc IS NOT NULL AND location IS NULL) OR "
            "(correction_type = 'ignore_event' AND raw_event_id IS NOT NULL "
            "AND event_type IS NULL AND event_timestamp IS NULL "
            "AND event_timestamp_utc IS NULL AND location IS NULL) OR "
            "(correction_type = 'manual_event' AND raw_event_id IS NULL "
            "AND event_type IS NOT NULL AND event_timestamp IS NOT NULL "
            "AND event_timestamp_utc IS NOT NULL AND location IS NOT NULL)",
            name="ck_work_event_corrections_shape",
        ),
        sa.ForeignKeyConstraint(["raw_event_id"], ["work_events.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("raw_event_id", name="uq_work_event_corrections_raw_event_id"),
    )
    op.create_index(
        "ix_work_event_corrections_event_timestamp_utc",
        "work_event_corrections",
        ["event_timestamp_utc"],
    )
    op.create_index(
        "ix_work_event_corrections_location",
        "work_event_corrections",
        ["location"],
    )


def downgrade() -> None:
    op.drop_index("ix_work_event_corrections_location", table_name="work_event_corrections")
    op.drop_index(
        "ix_work_event_corrections_event_timestamp_utc",
        table_name="work_event_corrections",
    )
    op.drop_table("work_event_corrections")

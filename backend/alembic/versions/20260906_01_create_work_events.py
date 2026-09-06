"""create work events

Revision ID: 20260906_01
Revises:
Create Date: 2026-09-06
"""
from alembic import op
import sqlalchemy as sa

revision = "20260906_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=10), nullable=False),
        sa.Column("location", sa.String(length=100), nullable=False),
        sa.Column("event_timestamp", sa.String(length=40), nullable=False),
        sa.Column("event_timestamp_utc", sa.String(length=40), nullable=False),
        sa.Column("received_at", sa.String(length=40), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_work_events_event_timestamp", "work_events", ["event_timestamp"])
    op.create_index("ix_work_events_event_timestamp_utc", "work_events", ["event_timestamp_utc"])
    op.create_index("ix_work_events_location", "work_events", ["location"])


def downgrade() -> None:
    op.drop_index("ix_work_events_location", table_name="work_events")
    op.drop_index("ix_work_events_event_timestamp_utc", table_name="work_events")
    op.drop_index("ix_work_events_event_timestamp", table_name="work_events")
    op.drop_table("work_events")

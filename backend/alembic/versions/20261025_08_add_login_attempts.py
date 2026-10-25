"""add bounded login attempt history, independent of users and work data

Revision ID: 20261025_08
Revises: 20261006_07
"""
from alembic import op
import sqlalchemy as sa

revision = "20261025_08"
down_revision = "20261006_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("login_key", sa.String(64), nullable=False),
        sa.Column("ip_key", sa.String(43), nullable=False),
        sa.Column("attempted_at", sa.String(40), nullable=False),
        sa.Column("failed_at", sa.String(40), nullable=True),
        sa.CheckConstraint(
            "length(login_key) = 64 AND login_key NOT GLOB '*[^0-9a-f]*'",
            name="ck_login_attempts_login_key",
        ),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_login_attempts_ip", "login_attempts", ["ip_key", "attempted_at"])
    op.create_index("ix_login_attempts_login", "login_attempts", ["login_key", "failed_at"])
    op.create_index("ix_login_attempts_cleanup", "login_attempts", ["attempted_at"])


def downgrade() -> None:
    op.drop_table("login_attempts")

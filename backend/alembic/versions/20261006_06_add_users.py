"""add isolated user identities without seeding credentials

Revision ID: 20261006_06
Revises: 20260909_05
"""
from alembic import op
import sqlalchemy as sa

revision = "20261006_06"
down_revision = "20260909_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "length(username) BETWEEN 3 AND 64 "
            "AND instr(username, char(0)) = 0 "
            "AND username NOT GLOB '*[^a-z0-9._-]*' "
            "AND substr(username, 1, 1) GLOB '[a-z0-9]'",
            name="ck_users_username",
        ),
        sa.CheckConstraint("length(password_hash) > 0", name="ck_users_password_hash"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )


def downgrade() -> None:
    op.drop_table("users")

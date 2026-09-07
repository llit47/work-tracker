"""add pay rates

Revision ID: 20260907_03
Revises: 20260907_02
Create Date: 2026-09-07
"""
from datetime import date, datetime, timezone
from alembic import op
import sqlalchemy as sa

revision = "20260907_03"
down_revision = "20260907_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pay_rates = op.create_table(
        "pay_rates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        # SQLite NUMERIC stores non-integral values as binary REAL. Canonical
        # fixed-point text keeps every configured decimal rate exact.
        sa.Column("hourly_rate", sa.String(length=20), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "hourly_rate GLOB '[0-9]*.[0-9][0-9]' "
            "AND hourly_rate NOT GLOB '*[^0-9.]*' "
            "AND length(hourly_rate) - length(replace(hourly_rate, '.', '')) = 1 "
            "AND CAST(hourly_rate AS NUMERIC) > 0 "
            "AND CAST(hourly_rate AS NUMERIC) <= 1000000.00",
            name="ck_pay_rates_hourly_rate",
        ),
        sa.CheckConstraint(
            "length(currency) = 3 AND currency GLOB '[A-Z][A-Z][A-Z]'",
            name="ck_pay_rates_currency",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("effective_from", name="uq_pay_rates_effective_from"),
    )
    op.bulk_insert(
        pay_rates,
        [
            {
                "effective_from": date(1970, 1, 1),
                "hourly_rate": "50.00",
                "currency": "PLN",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("pay_rates")

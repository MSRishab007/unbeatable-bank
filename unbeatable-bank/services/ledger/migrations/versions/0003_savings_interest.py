"""Savings interest: one accrual run per business date.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        -- One row per business date makes the daily accrual idempotent:
        -- running EOD twice for the same date accrues interest once.
        CREATE TABLE ledger.accrual_runs (
            business_date date        PRIMARY KEY,
            annual_rate   numeric(8, 6) NOT NULL CHECK (annual_rate >= 0 AND annual_rate < 1),
            accounts      integer     NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now()
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE ledger.accrual_runs")

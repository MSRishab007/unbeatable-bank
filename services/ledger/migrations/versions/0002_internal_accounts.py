"""Seed the bank's internal (general ledger) accounts.

These are the other side of every movement that doesn't go customer-to-customer:
simulated cash deposits, interest paid, penalties earned, tax withheld.
IDs are fixed so every service can refer to them (see app/internal_accounts.py).

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

INTERNAL = {
    "00000000-0000-0000-0000-000000000001": "CASH",
    "00000000-0000-0000-0000-000000000002": "INTEREST_EXPENSE",
    "00000000-0000-0000-0000-000000000003": "PENALTY_INCOME",
    "00000000-0000-0000-0000-000000000004": "TDS_PAYABLE",
    "00000000-0000-0000-0000-000000000005": "FEE_INCOME",
    "00000000-0000-0000-0000-000000000006": "SUSPENSE",
}


def upgrade() -> None:
    for account_id in INTERNAL:
        op.execute(
            f"""
            INSERT INTO ledger.balances (account_id, kind, product_type)
            VALUES ('{account_id}', 'INTERNAL', 'INTERNAL')
            ON CONFLICT (account_id) DO NOTHING
            """
        )


def downgrade() -> None:
    ids = ", ".join(f"'{i}'" for i in INTERNAL)
    op.execute(f"DELETE FROM ledger.balances WHERE account_id IN ({ids})")

"""How the payee was named, and transfers between the customer's own accounts.

payee_kind     ACCOUNT_NUMBER, PAY_ID or MOBILE (QR payments keep channel = QR_PAYMENT)
own_transfer   true when both accounts belong to the customer: such transfers do not
               count towards the daily limits

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
        ALTER TABLE intake.transactions
            ADD COLUMN payee_kind   text CHECK (payee_kind IN ('ACCOUNT_NUMBER', 'PAY_ID', 'MOBILE')),
            ADD COLUMN own_transfer boolean NOT NULL DEFAULT false;
        CREATE INDEX transactions_customer_payee ON intake.transactions (customer_id, to_account_id, created_at)
            WHERE status = 'COMPLETED';
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX intake.transactions_customer_payee")
    op.execute("ALTER TABLE intake.transactions DROP COLUMN own_transfer, DROP COLUMN payee_kind")

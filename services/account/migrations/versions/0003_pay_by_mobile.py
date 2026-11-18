"""Pay by mobile number, opt-in.

A customer may let others pay them by their registered mobile number. The money goes to
the account they choose; with none chosen, to the first account they opened that can
receive money.

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
        ALTER TABLE account.customers
            ADD COLUMN pay_by_mobile        boolean NOT NULL DEFAULT false,
            ADD COLUMN receiving_account_id uuid REFERENCES account.accounts (account_id);
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE account.customers DROP COLUMN receiving_account_id, DROP COLUMN pay_by_mobile")

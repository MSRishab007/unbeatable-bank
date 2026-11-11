"""Limit raises that wait a day, and fuller nominee details.

A customer can lower a limit at once, but a higher limit takes effect only after 24
hours: someone who has taken over the account can't raise the limit and empty it
straight away, and the real customer has a day to notice the notification.

Nominees get a guardian (required when the nominee is a minor) and who added them.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE account.accounts
            ADD COLUMN pending_daily_limit   bigint CHECK (pending_daily_limit > 0),
            ADD COLUMN pending_per_txn_limit bigint CHECK (pending_per_txn_limit > 0),
            ADD COLUMN pending_limits_at     timestamptz,
            ADD CONSTRAINT pending_limits_have_a_time CHECK (
                (pending_daily_limit IS NULL AND pending_per_txn_limit IS NULL) = (pending_limits_at IS NULL));

        ALTER TABLE account.nominees
            ADD COLUMN guardian_name text,
            ADD COLUMN added_by      text NOT NULL DEFAULT 'unknown';
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE account.nominees DROP COLUMN guardian_name, DROP COLUMN added_by;
        ALTER TABLE account.accounts DROP CONSTRAINT pending_limits_have_a_time,
            DROP COLUMN pending_daily_limit, DROP COLUMN pending_per_txn_limit, DROP COLUMN pending_limits_at;
        """
    )

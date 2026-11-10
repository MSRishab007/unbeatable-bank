"""Risk decisions, OTP challenges and fraud-review holds.

New statuses:
  CHALLENGE  waiting for the customer to confirm with an OTP
  HELD       funds reserved in the Ledger, waiting for a fraud analyst

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
        ALTER TABLE intake.transactions
            ADD COLUMN device_id     text,
            ADD COLUMN ip            text,
            ADD COLUMN limits        jsonb,          -- limits in force when the payment was checked
            ADD COLUMN payee_trusted boolean,
            ADD COLUMN risk_decision text CHECK (risk_decision IN ('ALLOW', 'CHALLENGE', 'HOLD', 'BLOCK')),
            ADD COLUMN risk_score    integer,
            ADD COLUMN risk_reasons  jsonb NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN challenge_passed boolean NOT NULL DEFAULT false,
            ADD COLUMN hold_id       uuid,
            ADD COLUMN review        text CHECK (review IN ('APPROVED', 'REJECTED')),
            ADD COLUMN reviewed_by   text;

        ALTER TABLE intake.transactions DROP CONSTRAINT transactions_status_check;
        ALTER TABLE intake.transactions ADD CONSTRAINT transactions_status_check
            CHECK (status IN ('PROCESSING', 'CHALLENGE', 'HELD', 'COMPLETED', 'DECLINED'));

        ALTER TABLE intake.transactions DROP CONSTRAINT decided_has_time;
        ALTER TABLE intake.transactions ADD CONSTRAINT decided_has_time
            CHECK ((status IN ('COMPLETED', 'DECLINED')) = (decided_at IS NOT NULL));
        ALTER TABLE intake.transactions ADD CONSTRAINT held_has_hold
            CHECK (status <> 'HELD' OR hold_id IS NOT NULL);

        DROP INDEX intake.txn_from_account_day;
        DROP INDEX intake.txn_customer_day;
        CREATE INDEX txn_from_account_day ON intake.transactions (from_account_id, created_at) WHERE reserved;
        CREATE INDEX txn_customer_day ON intake.transactions (customer_id, created_at) WHERE reserved;
        CREATE INDEX txn_open_reviews ON intake.transactions (created_at) WHERE status IN ('HELD', 'CHALLENGE');
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM intake.transactions WHERE status IN ('CHALLENGE', 'HELD');
        DROP INDEX intake.txn_open_reviews;
        ALTER TABLE intake.transactions DROP CONSTRAINT held_has_hold;
        ALTER TABLE intake.transactions DROP CONSTRAINT decided_has_time;
        ALTER TABLE intake.transactions ADD CONSTRAINT decided_has_time
            CHECK ((status = 'PROCESSING') = (decided_at IS NULL));
        ALTER TABLE intake.transactions DROP CONSTRAINT transactions_status_check;
        ALTER TABLE intake.transactions ADD CONSTRAINT transactions_status_check
            CHECK (status IN ('PROCESSING', 'COMPLETED', 'DECLINED'));
        ALTER TABLE intake.transactions
            DROP COLUMN device_id, DROP COLUMN ip, DROP COLUMN limits, DROP COLUMN payee_trusted,
            DROP COLUMN risk_decision, DROP COLUMN risk_score, DROP COLUMN risk_reasons,
            DROP COLUMN challenge_passed, DROP COLUMN hold_id, DROP COLUMN review, DROP COLUMN reviewed_by;
        """
    )

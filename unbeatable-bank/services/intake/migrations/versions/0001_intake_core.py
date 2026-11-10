"""Transaction Intake: one row per payment request and its decision.

COMPLETED rows, and PROCESSING rows that already passed the limit check
(reserved = true), count towards daily limits. The reservation is taken under a
lock before calling the Ledger, so two concurrent payments can't both slip
under the limit.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE SCHEMA IF NOT EXISTS intake;

        CREATE TABLE intake.transactions (
            txn_id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            customer_id       uuid        NOT NULL,          -- who initiated it
            idempotency_key   text        NOT NULL,          -- from the client, unique per customer
            request_hash      text        NOT NULL,
            channel           text        NOT NULL CHECK (channel IN ('TRANSFER', 'QR_PAYMENT')),
            from_account_id   uuid        NOT NULL,
            payee_ref         text        NOT NULL,          -- what the customer entered: account number or alias
            to_account_id     uuid,                          -- resolved payee
            to_account_number text,
            to_display_name   text,
            amount            bigint      NOT NULL CHECK (amount > 0),  -- paise
            note              text        CHECK (length(note) <= 140),
            status            text        NOT NULL DEFAULT 'PROCESSING'
                                  CHECK (status IN ('PROCESSING', 'COMPLETED', 'DECLINED')),
            decline_code      text,
            reserved          boolean     NOT NULL DEFAULT false,  -- passed limit checks; counts towards limits
            journal_id        uuid,
            created_at        timestamptz NOT NULL DEFAULT now(),
            updated_at        timestamptz NOT NULL DEFAULT now(),
            decided_at        timestamptz,
            UNIQUE (customer_id, idempotency_key),
            CONSTRAINT completed_has_journal CHECK (status <> 'COMPLETED' OR journal_id IS NOT NULL),
            CONSTRAINT declined_has_code CHECK (status <> 'DECLINED' OR decline_code IS NOT NULL),
            CONSTRAINT decided_has_time CHECK ((status = 'PROCESSING') = (decided_at IS NULL))
        );

        -- Daily-limit sums (per account, per customer) only look at today's rows.
        CREATE INDEX txn_from_account_day ON intake.transactions (from_account_id, created_at)
            WHERE status IN ('PROCESSING', 'COMPLETED');
        CREATE INDEX txn_customer_day ON intake.transactions (customer_id, created_at)
            WHERE status IN ('PROCESSING', 'COMPLETED');
        CREATE INDEX txn_customer_history ON intake.transactions (customer_id, created_at DESC);

        -- Transactional outbox -> Kafka topic txn-events (consumed by Flink, Fraud Alert, Notification).
        CREATE TABLE intake.outbox (
            event_id     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            topic        text        NOT NULL,
            event_key    text        NOT NULL,
            event_type   text        NOT NULL,
            payload      jsonb       NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),
            published_at timestamptz
        );
        CREATE INDEX intake_outbox_unpublished ON intake.outbox (created_at) WHERE published_at IS NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA intake CASCADE")

"""Fraud Alert: cases, their timeline, maker-checker approvals, signal correlation.

Alerts, the notification inbox and analytics snapshots are documents (Firestore).

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
        CREATE SCHEMA IF NOT EXISTS fraud;

        -- Events arrive at least once; each event id is handled once.
        CREATE TABLE fraud.processed_events (
            event_id     text        PRIMARY KEY,
            processed_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE SEQUENCE fraud.case_number_seq START 1001;

        CREATE TABLE fraud.cases (
            case_id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            case_number     text        NOT NULL UNIQUE DEFAULT 'CASE-' || nextval('fraud.case_number_seq'),
            type            text        NOT NULL CHECK (type IN
                                ('HELD_PAYMENT', 'CUSTOMER_REPORT', 'PATTERN', 'ACCOUNT_TAKEOVER')),
            severity        text        NOT NULL CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH')),
            status          text        NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'RESOLVED')),
            resolution      text        CHECK (resolution IN ('CONFIRMED_FRAUD', 'FALSE_POSITIVE')),
            customer_id     uuid,
            account_id      uuid,
            txn_id          uuid,
            summary         text        NOT NULL,
            reasons         jsonb       NOT NULL DEFAULT '[]'::jsonb,
            created_at      timestamptz NOT NULL DEFAULT now(),
            resolved_at     timestamptz,
            resolved_by     text,
            resolution_note text,
            CONSTRAINT resolved_is_complete CHECK ((status = 'RESOLVED') = (resolution IS NOT NULL))
        );
        CREATE UNIQUE INDEX one_case_per_txn ON fraud.cases (txn_id) WHERE txn_id IS NOT NULL;
        CREATE UNIQUE INDEX one_open_pattern_case ON fraud.cases (account_id)
            WHERE type = 'PATTERN' AND status = 'OPEN';
        CREATE INDEX cases_open ON fraud.cases (created_at DESC) WHERE status = 'OPEN';
        CREATE INDEX cases_by_customer ON fraud.cases (customer_id, created_at DESC);

        CREATE TABLE fraud.case_events (
            id         bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            case_id    uuid        NOT NULL REFERENCES fraud.cases (case_id),
            event_type text        NOT NULL,
            detail     jsonb       NOT NULL DEFAULT '{}'::jsonb,
            actor      text        NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX case_events_by_case ON fraud.case_events (case_id, id);

        -- Signals from stream analytics, kept briefly to correlate several on one account.
        CREATE TABLE fraud.signals (
            id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            signal_type text        NOT NULL,
            account_id  uuid        NOT NULL,
            value       numeric,
            created_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX signals_recent ON fraud.signals (account_id, created_at DESC);

        -- Maker-checker ("four eyes"): sensitive actions need a second member of staff.
        CREATE TABLE fraud.approvals (
            approval_id  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            action       text        NOT NULL CHECK (action IN ('UNFREEZE_ACCOUNT')),
            target_id    uuid        NOT NULL,
            reason       text        NOT NULL CHECK (length(reason) >= 5),
            requested_by text        NOT NULL,
            status       text        NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED')),
            decided_by   text,
            created_at   timestamptz NOT NULL DEFAULT now(),
            decided_at   timestamptz,
            CONSTRAINT different_person CHECK (decided_by IS NULL OR decided_by <> requested_by)
        );
        CREATE UNIQUE INDEX one_pending_per_target ON fraud.approvals (action, target_id) WHERE status = 'PENDING';
        """
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA fraud CASCADE")

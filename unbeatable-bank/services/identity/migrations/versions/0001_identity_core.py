"""Identity: credentials, devices, staff users and one-time passwords.

Sessions and login events are high-volume and semi-structured, so they live in
the document store (Firestore), not here.

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
        CREATE SCHEMA IF NOT EXISTS identity;

        -- How a customer logs in. The customer profile itself belongs to the Account service;
        -- customer_id is the shared key (no cross-database foreign key).
        CREATE TABLE identity.credentials (
            customer_id     uuid        PRIMARY KEY,
            email           text        NOT NULL UNIQUE CHECK (email = lower(email)),
            phone           text        NOT NULL UNIQUE,
            password_hash   text        NOT NULL,
            failed_attempts integer     NOT NULL DEFAULT 0,
            locked_until    timestamptz,
            last_login_at   timestamptz,
            last_login_lat  double precision,   -- where the last successful login came from
            last_login_lon  double precision,
            created_at      timestamptz NOT NULL DEFAULT now()
        );

        -- Every device a customer has logged in from.
        CREATE TABLE identity.devices (
            device_id        uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            customer_id      uuid        NOT NULL REFERENCES identity.credentials (customer_id),
            fingerprint_hash text        NOT NULL,
            label            text,
            first_seen       timestamptz NOT NULL DEFAULT now(),
            last_seen        timestamptz NOT NULL DEFAULT now(),
            last_ip          text,
            revoked_at       timestamptz,
            UNIQUE (customer_id, fingerprint_hash)
        );

        CREATE TABLE identity.staff_users (
            staff_id      text        PRIMARY KEY CHECK (staff_id ~ '^[a-z0-9_.-]{2,40}$'),
            password_hash text        NOT NULL,
            role          text        NOT NULL CHECK (role IN ('ADMIN', 'ANALYST', 'TELLER', 'KYC')),
            active        boolean     NOT NULL DEFAULT true,
            created_at    timestamptz NOT NULL DEFAULT now()
        );

        -- One-time passwords for step-up confirmation. Only a hash of the code is stored.
        CREATE TABLE identity.otps (
            otp_id      uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            customer_id uuid        NOT NULL,
            purpose     text        NOT NULL,       -- e.g. txn:<txn_id>
            code_hash   text        NOT NULL,
            attempts    integer     NOT NULL DEFAULT 0,
            expires_at  timestamptz NOT NULL,
            consumed_at timestamptz,
            created_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX otps_lookup ON identity.otps (customer_id, purpose, created_at DESC);

        CREATE TABLE identity.outbox (
            event_id     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            topic        text        NOT NULL,
            event_key    text        NOT NULL,
            event_type   text        NOT NULL,
            payload      jsonb       NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),
            published_at timestamptz
        );
        CREATE INDEX identity_outbox_unpublished ON identity.outbox (created_at) WHERE published_at IS NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA identity CASCADE")

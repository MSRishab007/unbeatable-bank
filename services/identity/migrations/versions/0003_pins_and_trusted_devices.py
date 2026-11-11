"""Two PINs per customer, and devices that must be confirmed before they are trusted.

PAYMENT (6 digits) approves money leaving: payments, new payees, higher limits.
APP (4 digits) approves deposits and nominees, and unlocks an idle session.

A device is trusted once the customer proves it is theirs: the device they sign up
on is trusted straight away, any later one needs a code sent to them first.

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
        CREATE TABLE identity.pins (
            customer_id     uuid        NOT NULL REFERENCES identity.credentials (customer_id),
            kind            text        NOT NULL CHECK (kind IN ('PAYMENT', 'APP')),
            pin_hash        text        NOT NULL,
            failed_attempts integer     NOT NULL DEFAULT 0,
            locked_until    timestamptz,
            set_at          timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (customer_id, kind)
        );

        ALTER TABLE identity.devices ADD COLUMN trusted_at timestamptz;
        -- Devices already in use were trusted under the old rules; keep them trusted.
        UPDATE identity.devices SET trusted_at = first_seen WHERE revoked_at IS NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE identity.devices DROP COLUMN trusted_at;
        DROP TABLE identity.pins;
        """
    )

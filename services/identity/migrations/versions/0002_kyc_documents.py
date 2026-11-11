"""KYC documents: what a customer uploaded to prove who they are, and what staff decided.

The files themselves are in object storage (a private bucket); this table holds
only where each file is, its SHA-256, and its review status. kyc_events is an
append-only record of every upload, staff view and decision.

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
        CREATE TABLE identity.kyc_documents (
            document_id   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            customer_id   uuid        NOT NULL,
            doc_type      text        NOT NULL CHECK (doc_type IN ('PAN', 'ADDRESS_PROOF', 'PHOTO')),
            content_type  text        NOT NULL CHECK (content_type IN ('image/jpeg', 'image/png', 'application/pdf')),
            size_bytes    integer     NOT NULL CHECK (size_bytes > 0),
            sha256        text        NOT NULL,
            object_path   text        NOT NULL,
            -- PENDING: waiting for staff. APPROVED: accepted. REJECTED: refused, file deleted.
            -- REPLACED: the customer uploaded a newer one before staff looked, file deleted.
            status        text        NOT NULL DEFAULT 'PENDING'
                              CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'REPLACED')),
            reject_reason text,
            uploaded_at   timestamptz NOT NULL DEFAULT now(),
            decided_at    timestamptz,
            decided_by    text,
            CHECK ((status = 'REJECTED') = (reject_reason IS NOT NULL))
        );
        -- At most one document of each type is in play (waiting or accepted) per customer.
        CREATE UNIQUE INDEX kyc_one_live_document ON identity.kyc_documents (customer_id, doc_type)
            WHERE status IN ('PENDING', 'APPROVED');
        CREATE INDEX kyc_pending ON identity.kyc_documents (uploaded_at) WHERE status = 'PENDING';
        CREATE INDEX kyc_by_hash ON identity.kyc_documents (sha256);

        CREATE TABLE identity.kyc_events (
            event_id     bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            customer_id  uuid        NOT NULL,
            document_id  uuid        REFERENCES identity.kyc_documents (document_id),
            event_type   text        NOT NULL,   -- UPLOADED, REPLACED, VIEWED, APPROVED, REJECTED, KYC_VERIFIED
            actor        text        NOT NULL,   -- 'customer:<id>' or 'staff:<id>:<ROLE>'
            detail       jsonb       NOT NULL DEFAULT '{}'::jsonb,
            created_at   timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX kyc_events_by_customer ON identity.kyc_events (customer_id, event_id);

        CREATE FUNCTION identity.forbid_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
        END $$;
        CREATE TRIGGER kyc_events_append_only BEFORE UPDATE OR DELETE ON identity.kyc_events
            FOR EACH ROW EXECUTE FUNCTION identity.forbid_change();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE identity.kyc_events;
        DROP TABLE identity.kyc_documents;
        DROP FUNCTION identity.forbid_change();
        """
    )

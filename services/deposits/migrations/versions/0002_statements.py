"""Statements: the register of monthly account statements the bank has issued.

The PDF itself is in object storage. A row records which entries it covered
(entries_hash) and exactly which file was issued (pdf_sha256), so a statement can
later be checked against the ledger, and a PDF someone presents can be checked
against what the bank really issued.

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
        CREATE TABLE deposits.statements (
            statement_id    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id      uuid        NOT NULL,
            account_number  text        NOT NULL,
            period          text        NOT NULL CHECK (period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
            date_from       date        NOT NULL,
            date_to         date        NOT NULL,
            opening_balance bigint      NOT NULL,
            closing_balance bigint      NOT NULL,
            entry_count     integer     NOT NULL,
            entries_hash    text        NOT NULL,
            pdf_sha256      text        NOT NULL,
            object_path     text        NOT NULL,
            issued_on       date        NOT NULL,          -- the bank's business date when it was issued
            created_at      timestamptz NOT NULL DEFAULT now(),
            UNIQUE (account_id, period)
        );
        CREATE INDEX statements_by_file ON deposits.statements (pdf_sha256);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE deposits.statements")
